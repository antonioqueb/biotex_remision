from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Preserve every existing application and its historical consumption. No stock/accounting events are created.
    cr.execute("""UPDATE biotex_remision_mask m SET application_state='legacy',
                  legacy_amount=m.amount, source_amount=m.amount
                  FROM biotex_remision r WHERE r.id=m.remision_id
                  AND r.state IN ('delivered','signed','invoiced') AND m.application_state='draft'""")
    env = api.Environment(cr, SUPERUSER_ID, {'tracking_disable': True})
    masks = env['biotex.remision.mask'].search([])
    masks.invalidate_recordset()
    masks.modified(['application_state', 'legacy_amount', 'source_amount'])
    # Link only unambiguous existing moves; retain ambiguous cases without reconstructing deliveries.
    for rec in env['biotex.remision'].search([('picking_id', '!=', False)]):
        for line in rec.line_ids:
            candidates = rec.picking_id.move_ids.filtered(lambda m: m.product_id == line.product_id
                and not m.biotex_remision_line_id and m.product_uom == line.product_uom_id
                and m.product_uom_qty == line.product_qty)
            matching_lines = rec.line_ids.filtered(lambda l: l.product_id == line.product_id
                and l.product_uom_id == line.product_uom_id and l.product_qty == line.product_qty)
            if len(candidates) == 1 and len(matching_lines) == 1:
                candidates.biotex_remision_line_id = line
    env.flush_all()
