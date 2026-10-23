# Copyright 2026 Thousan Wang
# License LGPL-3.0 (https://www.gnu.org/licenses/lgpl-3.0.html).
"""Idempotent shipment application for DistributionGo (GO gate O2).

`distributiongo_apply_result` is the one public method DistributionGo calls. Everything it
does happens in the single Odoo transaction of the JSON-2 request:

    look up the receipt -> check picking state and expected version -> write the picked
    quantities -> button_validate (backorder policy in the context) -> write the receipt

Retrying with the same `operation_key` returns the stored outcome without touching stock.
The method runs as the calling user; it never uses sudo() to widen access.
"""

import hashlib
import json

from odoo import _, api, models
from odoo.exceptions import UserError


def _line_payload(lines):
    """Normalise the request so the same content always hashes the same way."""
    normalised = []
    for line in lines or []:
        normalised.append({
            'move_line_id': int(line['move_line_id']),
            'quantity': float(line['quantity']),
            'lot_name': (line.get('lot_name') or '') or None,
        })
    normalised.sort(key=lambda item: item['move_line_id'])
    return normalised


def _request_hash(picking_id, lines, backorder_policy, expected_version):
    payload = json.dumps({
        'picking_id': picking_id,
        'lines': _line_payload(lines),
        'backorder_policy': backorder_policy,
        'expected_version': expected_version,
    }, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


# ADR-014 U3: DistributionGo picks for one-step deliveries only. In two- and three-step
# routes the goods leave stock in an internal pick operation before the delivery, so
# picking against the delivery would be wrong; those warehouses are reported, not served.
SUPPORTED_DELIVERY_STEPS = ('ship_only',)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def distributiongo_version(self):
        """A value that changes when the reservation DistributionGo snapshotted changes.

        Covers the picking state and every move line id, product, quantity and lot, so a
        reservation edited in Odoo after the snapshot is detected before anything is
        written (the Odoo equivalent of the InvenTree allocation comparison).
        """
        self.ensure_one()
        payload = {
            'state': self.state,
            'lines': sorted(
                [
                    {
                        'id': line.id,
                        'product_id': line.product_id.id,
                        'quantity': float(line.quantity),
                        'lot_id': line.lot_id.id or None,
                    }
                    for line in self.move_line_ids
                ],
                key=lambda item: item['id'],
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()

    def _distributiongo_order_reference(self):
        """The source document number (the sales order for sale-created deliveries).

        Read from ``origin`` rather than ``sale_id``: the DistributionGo bot holds stock
        rights only, and following ``sale_id`` would need sales access (no sudo()).
        """
        self.ensure_one()
        return self.origin or self.name

    def _distributiongo_supported_route(self):
        """A delivery of a one-step warehouse (U3); pickings without a warehouse count too."""
        self.ensure_one()
        warehouse = self.picking_type_id.warehouse_id
        return not warehouse or warehouse.delivery_steps in SUPPORTED_DELIVERY_STEPS

    def _distributiongo_fully_reserved(self):
        """Outgoing, on a supported route, and every open move reserved in full (Q3).

        Under the default "as soon as possible" shipping policy Odoo already calls a
        delivery 'assigned' when only part of it is reserved; that is not ready for us.
        """
        self.ensure_one()
        open_moves = self.move_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
        return (
            self.picking_type_code == 'outgoing'
            and self._distributiongo_supported_route()
            and self.state == 'assigned'
            and bool(open_moves)
            and all(move.state == 'assigned' for move in open_moves)
        )

    def distributiongo_snapshot(self):
        """Read model for DistributionGo: what can be picked, plus the version.

        Everything comes from one call, so the lines and the version always belong to
        the same moment; DistributionGo stores both and the release must still match.
        """
        self.ensure_one()
        return {
            'picking_id': self.id,
            'name': self.name,
            'state': self.state,
            'ready': self._distributiongo_fully_reserved(),
            'closed': self.state in ('done', 'cancel'),
            'order_reference': self._distributiongo_order_reference(),
            'customer_name': self.partner_id.display_name or '',
            'scheduled_date': self.scheduled_date and self.scheduled_date.isoformat(),
            'version': self.distributiongo_version(),
            'lines': [
                {
                    'move_line_id': line.id,
                    'move_id': line.move_id.id,
                    'product_id': line.product_id.id,
                    'product_code': line.product_id.default_code or '',
                    'product_name': line.product_id.name or '',
                    'barcode': line.product_id.barcode or '',
                    'uom': line.product_uom_id.name or '',
                    'quantity': float(line.quantity),
                    'picked': line.picked,
                    'lot_id': line.lot_id.id or None,
                    'lot_name': line.lot_id.name or None,
                    'tracking': line.product_id.tracking,
                    'location_id': line.location_id.id or None,
                    'location_name': line.location_id.complete_name or '',
                }
                for line in self.move_line_ids
            ],
        }

    @api.model
    def distributiongo_ready_pickings(self, search='', customer='', due_before=None,
                                      exclude_ids=None, limit=50, offset=0):
        """Order pool for DistributionGo: outgoing deliveries Odoo has fully reserved.

        Only state 'assigned' qualifies (decision Q3); partly available work waits until
        Odoo reserves the rest. ``due_before`` is an ISO date and includes that day.
        """
        domain = [('picking_type_code', '=', 'outgoing'), ('state', '=', 'assigned'),
                  '|', ('picking_type_id.warehouse_id', '=', False),
                  ('picking_type_id.warehouse_id.delivery_steps', 'in', SUPPORTED_DELIVERY_STEPS)]
        partly_reserved = self.env['stock.move'].search([
            ('picking_id.picking_type_code', '=', 'outgoing'),
            ('picking_id.state', '=', 'assigned'),
            ('state', 'not in', ('assigned', 'done', 'cancel')),
        ]).picking_id.ids
        excluded = set(partly_reserved) | {int(pk) for pk in exclude_ids or ()}
        if excluded:
            domain.append(('id', 'not in', sorted(excluded)))
        if search:
            domain += ['|', '|', ('name', 'ilike', search), ('origin', 'ilike', search),
                       ('partner_id.name', 'ilike', search)]
        if customer:
            domain.append(('partner_id.name', 'ilike', customer))
        if due_before:
            domain.append(('scheduled_date', '<=', f'{due_before} 23:59:59'))
        limit = max(1, min(int(limit), 200))
        pickings = self.search(domain, order='scheduled_date asc, id asc', limit=limit, offset=int(offset))
        return {
            'count': self.search_count(domain),
            'results': [
                {
                    'picking_id': picking.id,
                    'name': picking.name,
                    'order_reference': picking._distributiongo_order_reference(),
                    'customer_name': picking.partner_id.display_name or '',
                    'scheduled_date': picking.scheduled_date and picking.scheduled_date.isoformat(),
                    'line_count': len(picking.move_line_ids),
                    'state': picking.state,
                }
                for picking in pickings
            ],
        }

    @api.model
    def distributiongo_route_check(self):
        """Installation and health check (U3): each warehouse's delivery route and
        whether DistributionGo serves it."""
        return [
            {
                'warehouse_id': warehouse.id,
                'code': warehouse.code,
                'name': warehouse.name,
                'delivery_steps': warehouse.delivery_steps,
                'supported': warehouse.delivery_steps in SUPPORTED_DELIVERY_STEPS,
            }
            for warehouse in self.env['stock.warehouse'].search([], order='id')
        ]

    def _distributiongo_receipt(self, operation_key):
        return self.env['distributiongo.receipt'].search([('operation_key', '=', operation_key)], limit=1)

    def _distributiongo_store(self, operation_key, request_hash, outcome, result):
        self.env['distributiongo.receipt'].create({
            'operation_key': operation_key,
            'picking_id': self.id,
            'request_hash': request_hash,
            'outcome': outcome,
            'result': result,
            'dg_actor': self.env.context.get('distributiongo_actor') or '',
        })
        return result

    @api.model
    def _distributiongo_conflict(self, reason, **detail):
        return {'outcome': 'conflict', 'reason': reason, **detail}

    def distributiongo_apply_result(self, operation_key, lines, backorder_policy='create', expected_version=None,
                                    actor=None):
        """Apply a DistributionGo pick result to this delivery, exactly once.

        Arguments:
            operation_key: DistributionGo posting command id; the idempotency key.
            lines: [{'move_line_id': int, 'quantity': float, 'lot_name': str|None}]
            backorder_policy: 'create' (default) or 'none' for the remainder.
            expected_version: value from distributiongo_snapshot(); refuses if it changed.
            actor: the DistributionGo user who released the shipment, kept on the receipt
                (Odoo only sees the DistributionGo bot); not part of the request identity.

        Returns a JSON-serialisable dict: outcome 'applied' (with picking state, done
        quantities and backorders) or 'conflict' with a reason. Never raises for business
        conflicts, so DistributionGo can record them instead of retrying forever.
        """
        self.ensure_one()
        self = self.with_context(distributiongo_actor=actor or '')
        if not operation_key:
            raise UserError(_('operation_key is required'))

        request_hash = _request_hash(self.id, lines, backorder_policy, expected_version)
        receipt = self._distributiongo_receipt(operation_key)
        if receipt:
            if receipt.request_hash != request_hash:
                return self._distributiongo_conflict(
                    'operation_key_reused_with_different_request',
                    operation_key=operation_key,
                    stored_picking_id=receipt.picking_id.id,
                )
            return {**receipt.result, 'duplicate': True}

        if self.state == 'done':
            return self._distributiongo_store(
                operation_key, request_hash, 'conflict',
                self._distributiongo_conflict('picking_already_done', picking_state=self.state),
            )
        if self.state in ('cancel', 'draft'):
            return self._distributiongo_store(
                operation_key, request_hash, 'conflict',
                self._distributiongo_conflict('picking_not_ready', picking_state=self.state),
            )
        if expected_version and expected_version != self.distributiongo_version():
            return self._distributiongo_store(
                operation_key, request_hash, 'conflict',
                self._distributiongo_conflict(
                    'picking_changed_in_odoo',
                    picking_state=self.state,
                    current_version=self.distributiongo_version(),
                ),
            )

        by_id = {line.id: line for line in self.move_line_ids}
        payload = _line_payload(lines)
        unknown = [item['move_line_id'] for item in payload if item['move_line_id'] not in by_id]
        if unknown:
            return self._distributiongo_store(
                operation_key, request_hash, 'conflict',
                self._distributiongo_conflict('unknown_move_lines', move_line_ids=unknown),
            )

        for item in payload:
            move_line = by_id[item['move_line_id']]
            values = {'quantity': item['quantity'], 'picked': item['quantity'] > 0}
            if item['lot_name']:
                if move_line.product_id.tracking == 'none':
                    return self._distributiongo_store(
                        operation_key, request_hash, 'conflict',
                        self._distributiongo_conflict('lot_not_tracked', move_line_id=move_line.id),
                    )
                values['lot_name'] = item['lot_name']
            move_line.write(values)
        for move_line in self.move_line_ids:
            if move_line.id not in {item['move_line_id'] for item in payload}:
                move_line.write({'quantity': 0, 'picked': False})

        if not any(item['quantity'] > 0 for item in payload):
            return self._distributiongo_store(
                operation_key, request_hash, 'conflict',
                self._distributiongo_conflict('nothing_picked'),
            )

        # skip_backorder answers the backorder wizard inside this transaction; without it
        # button_validate would return an action and nothing would be validated.
        context = {'skip_backorder': True}
        if backorder_policy == 'none':
            context['picking_ids_not_to_backorder'] = self.ids
        elif backorder_policy != 'create':
            raise UserError(_('backorder_policy must be "create" or "none"'))

        backorders_before = self.env['stock.picking'].search([('backorder_id', '=', self.id)])
        validation = self.with_context(**context).button_validate()
        if isinstance(validation, dict) and validation.get('type') == 'ir.actions.act_window':
            # Odoo wants a wizard: treat as a conflict rather than guessing an answer.
            raise UserError(_('button_validate requested %s; the PoC does not answer wizards')
                            % validation.get('res_model'))
        backorders = self.env['stock.picking'].search([('backorder_id', '=', self.id)]) - backorders_before

        result = {
            'outcome': 'applied',
            'operation_key': operation_key,
            'picking_id': self.id,
            'picking_state': self.state,
            'moves': [
                {
                    'move_id': move.id,
                    'product_id': move.product_id.id,
                    'demand': float(move.product_uom_qty),
                    'done': float(move.quantity if move.picked else 0.0),
                }
                for move in self.move_ids
            ],
            'backorders': [{'id': picking.id, 'name': picking.name} for picking in backorders],
            'duplicate': False,
        }
        return self._distributiongo_store(operation_key, request_hash, 'applied', result)
