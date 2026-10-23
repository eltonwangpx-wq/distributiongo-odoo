# Copyright 2026 Thousan Wang
# License LGPL-3.0 (https://www.gnu.org/licenses/lgpl-3.0.html).
"""Add-on tests in Odoo's own runner (WP-F1).

    odoo -d <db> -i distributiongo_connector --test-enable \
        --test-tags /distributiongo_connector --stop-after-init

or ``bash tests/v2/env/v2.sh addon-tests``. The public methods run as a user with only
the rights the DistributionGo bot gets (stock user + connector group), so a method that
would need more fails here first.
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'distributiongo')
class TestDistributionGoConnector(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.customers = cls.env.ref('stock.stock_location_customers')
        cls.customer = cls.env['res.partner'].create({'name': 'DG test customer'})
        groups = 'group_ids' if 'group_ids' in cls.env['res.users']._fields else 'groups_id'
        cls.bot = cls.env['res.users'].create({
            'name': 'DG test bot', 'login': 'dg-addon-test-bot',
            groups: [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('stock.group_stock_user').id,
                cls.env.ref('distributiongo_connector.group_distributiongo_connector').id,
            ])],
        })

    def _product(self, name, on_hand, tracking='none', lot=None):
        product = self.env['product.product'].create({
            'name': name, 'type': 'consu', 'is_storable': True, 'tracking': tracking,
        })
        lot_record = self.env['stock.lot'].create({'name': lot, 'product_id': product.id}) if lot else None
        self.env['stock.quant']._update_available_quantity(product, self.stock, on_hand, lot_id=lot_record)
        return product

    def _delivery(self, *lines):
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.out_type_id.id,
            'partner_id': self.customer.id,
            'location_id': self.stock.id,
            'location_dest_id': self.customers.id,
            'origin': 'SO-DG-TEST',
            'move_ids': [(0, 0, {
                'product_id': product.id, 'product_uom_qty': qty, 'product_uom': product.uom_id.id,
                'location_id': self.stock.id, 'location_dest_id': self.customers.id,
            }) for product, qty in lines],
        })
        picking.action_confirm()
        picking.action_assign()
        return picking

    def _as_bot(self, picking):
        return picking.with_user(self.bot)

    def _pickings_as_bot(self):
        return self.env['stock.picking'].with_user(self.bot)

    def _lines(self, picking, quantities, lot=None):
        return [{'move_line_id': line.id, 'quantity': qty, 'lot_name': lot}
                for line, qty in zip(picking.move_line_ids.sorted('id'), quantities)]

    def _on_hand(self, product):
        return product.with_context(location=self.stock.id).qty_available

    def test_snapshot_ready_and_order_pool(self):
        product = self._product('DG full', 10)
        picking = self._delivery((product, 4))
        snapshot = self._as_bot(picking).distributiongo_snapshot()
        self.assertTrue(snapshot['ready'])
        self.assertEqual(snapshot['order_reference'], 'SO-DG-TEST')
        self.assertEqual([line['quantity'] for line in snapshot['lines']], [4.0])
        pool = self._pickings_as_bot().distributiongo_ready_pickings(search=picking.name)
        self.assertEqual([row['picking_id'] for row in pool['results']], [picking.id])

    def test_partly_reserved_is_not_ready(self):
        product = self._product('DG partial', 2)
        picking = self._delivery((product, 5))
        self.assertEqual(picking.state, 'assigned')  # Odoo's "as soon as possible" policy
        self.assertFalse(self._as_bot(picking).distributiongo_snapshot()['ready'])
        pool = self._pickings_as_bot().distributiongo_ready_pickings(search=picking.name)
        self.assertEqual(pool['results'], [])

    def test_apply_ships_once_and_records_the_actor(self):
        product = self._product('DG once', 10)
        picking = self._delivery((product, 3))
        bot_picking = self._as_bot(picking)
        version = bot_picking.distributiongo_version()
        lines = self._lines(picking, [3])
        first = bot_picking.distributiongo_apply_result('dg-op-1', lines, expected_version=version, actor='alice')
        again = bot_picking.distributiongo_apply_result('dg-op-1', lines, expected_version=version, actor='alice')
        self.assertEqual(first['outcome'], 'applied')
        self.assertFalse(first['duplicate'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(picking.state, 'done')
        self.assertEqual(self._on_hand(product), 7)
        receipt = self.env['distributiongo.receipt'].search([('operation_key', '=', 'dg-op-1')])
        self.assertEqual(len(receipt), 1)
        self.assertEqual(receipt.dg_actor, 'alice')

    def test_same_key_with_different_content_is_a_conflict(self):
        product = self._product('DG reuse', 10)
        picking = self._delivery((product, 3))
        bot_picking = self._as_bot(picking)
        bot_picking.distributiongo_apply_result('dg-op-2', self._lines(picking, [3]))
        other = bot_picking.distributiongo_apply_result('dg-op-2', self._lines(picking, [2]))
        self.assertEqual(other['reason'], 'operation_key_reused_with_different_request')
        self.assertEqual(self._on_hand(product), 7)

    def test_changed_reservation_conflicts_without_shipping(self):
        product = self._product('DG changed', 10)
        picking = self._delivery((product, 4))
        version = self._as_bot(picking).distributiongo_version()
        picking.move_line_ids.write({'quantity': 3})  # an Odoo user edits the reservation
        result = self._as_bot(picking).distributiongo_apply_result(
            'dg-op-3', self._lines(picking, [3]), expected_version=version)
        self.assertEqual(result['reason'], 'picking_changed_in_odoo')
        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(self._on_hand(product), 10)

    def test_short_pick_backorder_policy(self):
        product = self._product('DG short', 20)
        keep = self._delivery((product, 3))
        drop = self._delivery((product, 3))
        kept = self._as_bot(keep).distributiongo_apply_result(
            'dg-op-4', self._lines(keep, [1]), backorder_policy='create')
        dropped = self._as_bot(drop).distributiongo_apply_result(
            'dg-op-5', self._lines(drop, [1]), backorder_policy='none')
        self.assertEqual(len(kept['backorders']), 1)
        self.assertEqual(dropped['backorders'], [])
        self.assertEqual(self._on_hand(product), 18)

    def test_lot_is_written_on_the_move_line(self):
        product = self._product('DG lot', 5, tracking='lot', lot='LOT-DG-1')
        picking = self._delivery((product, 2))
        result = self._as_bot(picking).distributiongo_apply_result(
            'dg-op-6', self._lines(picking, [2], lot='LOT-DG-1'))
        self.assertEqual(result['outcome'], 'applied')
        self.assertEqual(picking.move_line_ids.lot_id.mapped('name'), ['LOT-DG-1'])

    def test_multi_step_route_is_reported_and_not_served(self):
        product = self._product('DG route', 10)
        picking = self._delivery((product, 2))
        self.warehouse.delivery_steps = 'pick_ship'
        check = {row['warehouse_id']: row for row in self._pickings_as_bot().distributiongo_route_check()}
        self.assertFalse(check[self.warehouse.id]['supported'])
        self.assertFalse(self._as_bot(picking).distributiongo_snapshot()['ready'])
        pool = self._pickings_as_bot().distributiongo_ready_pickings(search=picking.name)
        self.assertEqual(pool['results'], [])
