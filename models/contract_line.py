from odoo import api, fields, models


class BiotexContractLine(models.Model):
    _inherit = 'biotex.contract.line'

    mask_ids = fields.One2many('biotex.remision.mask', 'contract_line_id', string='Aplicaciones de remisión')
    amount_applied = fields.Monetary(compute='_compute_delivered', store=True, string='Aplicación administrativa')
    physical_undetermined = fields.Boolean(compute='_compute_delivered', store=True, string='Unidades no determinadas')

    @api.depends('product_qty', 'price_unit', 'mask_ids.product_qty', 'mask_ids.application_state',
                 'mask_ids.amount', 'mask_ids.legacy_amount', 'mask_ids.invoice_line_id.move_id.state',
                 'mask_ids.line_id.qty_net', 'mask_ids.correspondence')
    def _compute_delivered(self):
        for line in self:
            active = line.mask_ids.filtered(lambda m: m.application_state in ('applied', 'review'))
            held = line.mask_ids.filtered(lambda m: m.application_state in ('legacy', 'validated') and m.legacy_amount)
            identity = active.filtered(lambda m: m.correspondence != 'substitution' and m.application_state == 'applied')
            documented = active.filtered(lambda m: m.invoice_line_id.move_id.state == 'posted')
            line.qty_delivered = sum(identity.mapped('product_qty'))
            line.qty_invoiced = sum(documented.filtered(lambda m: m.correspondence != 'substitution').mapped('product_qty'))
            line.qty_remaining = line.product_qty - line.qty_delivered
            line.amount_delivered = sum(identity.mapped('source_amount'))
            line.amount_applied = sum(active.mapped('amount')) + sum(held.mapped('legacy_amount'))
            line.amount_invoiced = sum(documented.mapped('amount'))
            line.progress = line.amount_applied / line.amount * 100 if line.amount else 0
            line.physical_undetermined = bool(held or active.filtered(lambda m: m.correspondence == 'substitution' or m.application_state == 'review'))

    def action_view_remisions(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Remisiones de la clave %s' % self.code,
                'res_model': 'biotex.remision', 'view_mode': 'list,form',
                'domain': [('id', 'in', self.mask_ids.mapped('remision_id').ids)]}
