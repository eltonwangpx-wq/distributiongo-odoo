# Copyright 2026 DistributionGo
# License LGPL-3.0 (https://www.gnu.org/licenses/lgpl-3.0.html).
# DistributionGo add-on for Odoo 19 (ADR-011, ADR-014). Version = Odoo series + product version.
# Published from this folder to github.com/eltonwangpx-wq/distributiongo-odoo (ADR-021).
{
    'name': 'DistributionGo Connector',
    'summary': 'Snapshots, order pool and idempotent shipment application for DistributionGo',
    'description': """
Narrow connector for DistributionGo: a one-call snapshot of a delivery, the order pool of
fully reserved deliveries, and one method that applies a pick result to a
delivery in a single Odoo transaction and records a receipt, so a lost response can be
resolved by the same operation key instead of guessing from picking state.
""",
    'version': '19.0.2.1.0',
    'category': 'Inventory/Inventory',
    'author': 'DistributionGo',
    'website': 'https://github.com/eltonwangpx-wq/distributiongo-odoo',
    'license': 'LGPL-3',
    'depends': ['stock'],
    'data': [
        'security/distributiongo_security.xml',
        'security/ir.model.access.csv',
    ],
    'installable': True,
    'application': False,
}
