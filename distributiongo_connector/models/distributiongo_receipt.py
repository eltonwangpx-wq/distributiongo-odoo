# Copyright 2026 Thousan Wang
# License LGPL-3.0 (https://www.gnu.org/licenses/lgpl-3.0.html).
"""Receipt of one DistributionGo operation applied to Odoo.

The receipt is what makes a lost response recoverable: DistributionGo retries with the
same operation key and gets the original outcome back instead of applying twice.
"""

from odoo import fields, models


class DistributionGoReceipt(models.Model):
    _name = 'distributiongo.receipt'
    _description = 'DistributionGo operation receipt'
    _order = 'id desc'

    operation_key = fields.Char(required=True, index=True, readonly=True)
    picking_id = fields.Many2one('stock.picking', required=True, readonly=True, ondelete='restrict')
    request_hash = fields.Char(required=True, readonly=True)
    outcome = fields.Selection(
        [('applied', 'Applied'), ('conflict', 'Conflict')],
        required=True,
        readonly=True,
    )
    result = fields.Json(required=True, readonly=True)
    applied_by = fields.Many2one('res.users', required=True, readonly=True, default=lambda self: self.env.user)
    # The DistributionGo user behind the release; applied_by is always the DG bot.
    dg_actor = fields.Char(readonly=True)

    # Odoo 19 replaced _sql_constraints with models.Constraint attributes.
    _operation_key_unique = models.Constraint(
        'unique(operation_key)',
        'The DistributionGo operation key must be unique.',
    )
