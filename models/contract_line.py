from odoo import api, fields, models


class BiotexContractLine(models.Model):
    _inherit = 'biotex.contract.line'

    mask_ids = fields.One2many('biotex.remision.mask', 'contract_line_id', string='Remisionado')

    def _get_delivered_values(self):
        delivered = self.mask_ids.filtered(lambda m: m.state in ('delivered', 'signed', 'invoiced'))
        invoiced = delivered.filtered(lambda m: m.state == 'invoiced')
        return sum(delivered.mapped('product_qty')), sum(invoiced.mapped('product_qty'))

    @api.depends('product_qty', 'price_unit', 'mask_ids.product_qty', 'mask_ids.state')
    def _compute_delivered(self):
        return super()._compute_delivered()

    def action_view_remisions(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Remisiones de la clave %s' % self.code,
                'res_model': 'biotex.remision', 'view_mode': 'list,form',
                'domain': [('id', 'in', self.mask_ids.mapped('remision_id').ids)]}
