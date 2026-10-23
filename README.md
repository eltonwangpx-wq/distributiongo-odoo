# DistributionGo Connector for Odoo

The Odoo module that lets [DistributionGo](https://www.thousan.com/distributiongo/) work with
Odoo deliveries. DistributionGo is a free warehouse-execution system: it turns reserved Odoo
deliveries into picking tasks for warehouse staff using the
[SortingGo App](https://www.thousan.com/sortinggo/), then ships the picked quantities back into Odoo.

This repository holds released versions only. Each Odoo series has its own branch (`19.0`),
and each release is a tag named after the module version (`19.0.2.1.0`).

## What the module does

DistributionGo calls these methods through Odoo's JSON-2 API as a dedicated bot user:

| Method on `stock.picking` | Purpose | Reads or writes |
|---|---|---|
| `distributiongo_version` | Module version and capabilities | Reads |
| `distributiongo_ready_pickings` | Order pool: outgoing deliveries Odoo has fully reserved | Reads |
| `distributiongo_snapshot` | One delivery's lines, quantities, locations and version, from the same moment | Reads |
| `distributiongo_route_check` | Which warehouses use a delivery route DistributionGo serves | Reads |
| `distributiongo_apply_result` | Ships the picked quantities in one transaction and stores a receipt under the operation key | **The only write** |

It also adds the `distributiongo.receipt` model. Sending the same operation key twice returns
the stored result instead of shipping again, so a lost network answer never causes a double
shipment.

The module runs as the calling user and never uses `sudo()`. It makes no outbound
connections and does not know where DistributionGo runs: DistributionGo calls Odoo, and
access is granted by the bot's API key and revoked by deleting it. It depends on `stock` only.

## Requirements

- Odoo 19.0, self-hosted or on Odoo.sh. Odoo Online cannot install custom modules and is not supported.
- One-step deliveries (warehouse delivery route "Deliver goods directly").
- DistributionGo 2.0 or later.

## Install

**Self-hosted Odoo**: put the `distributiongo_connector` folder in a directory on your
`addons_path`, either from a clone of the branch:

```bash
git clone --branch 19.0 https://github.com/eltonwangpx-wq/distributiongo-odoo.git
```

or from a release archive (Releases or Tags on GitHub). Restart Odoo, update the apps list
and install **DistributionGo Connector**.

**Odoo.sh**: add this repository to your project repository as a submodule on the matching
branch, push, then install the module:

```bash
git submodule add -b 19.0 https://github.com/eltonwangpx-wq/distributiongo-odoo.git distributiongo-odoo
```

## Connect DistributionGo

1. Create a user for DistributionGo, for example `dg-bot`, with only these rights: Internal
   User, Inventory / User and **DistributionGo Connector**.
2. Signed in as that user, create an API key (My Preferences, API Keys). Odoo 19 asks for an
   expiry date; DistributionGo warns before it expires.
3. In DistributionGo, set `DG_ODOO_URL` (your Odoo address), `DG_ODOO_DB` (the database name)
   and `DG_ODOO_KEY` (that API key), then restart its services.

## Versions

| Module version | Odoo | Notes |
|---|---|---|
| 19.0.2.1.1 | 19.0 | Copyright holder Thousan Wang; no functional change |
| 19.0.2.1.0 | 19.0 | Adds the route check |
| 19.0.2.0.0 | 19.0 | First release for DistributionGo 2.0 (not published here) |

A new public method raises the minor version, and DistributionGo keeps working with an older
module. A new Odoo series gets its own branch after it passes DistributionGo's provider
contract tests.

## Support and contributions

Please report problems through GitHub issues; support is best effort. Pull requests are not
accepted for now: the module is developed together with DistributionGo's tests and published
here from there.

## License

LGPL-3 (see `LICENSE`, which supplements the GNU GPL v3 in `COPYING`). Copyright 2026 Thousan Wang.
