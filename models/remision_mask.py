from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BiotexRemisionMask(models.Model):
    """Máscara: clave del contrato con la que se cobra lo entregado ("te entrego A por B", casado por monto)."""
    _name = 'biotex.remision.mask'
    _description = 'Clave a cobrar'
    _order = 'remision_id, id'

    remision_id = fields.Many2one('biotex.remision', required=True, ondelete='cascade', index=True)
    state = fields.Selection(related='remision_id.state', store=True)
    line_id = fields.Many2one(
        'biotex.remision.line', string='Producto entregado', domain="[('remision_id', '=', parent.id)]",
        help='Opcional: qué línea física cubre esta clave.')
    contract_id = fields.Many2one(related='remision_id.contract_id', store=True)
    contract_line_id = fields.Many2one(
        'biotex.contract.line', string='Clave del contrato', required=True, index=True,
        domain="[('contract_id', '=', parent.contract_id)]")
    code = fields.Char(related='contract_line_id.code')
    product_qty = fields.Float(string='Cantidad a cobrar', required=True, default=1.0)
    qty_remaining = fields.Float(related='contract_line_id.qty_remaining', string='Pendiente de la clave')
    price_unit = fields.Float(related='contract_line_id.price_unit', string='Precio contrato')
    currency_id = fields.Many2one(related='remision_id.currency_id')
    amount = fields.Monetary(compute='_compute_amount', store=True)

    @api.depends('product_qty', 'contract_line_id.price_unit')
    def _compute_amount(self):
        for m in self:
            m.amount = m.product_qty * m.contract_line_id.price_unit

    @api.constrains('product_qty')
    def _check_qty(self):
        for m in self:
            if m.product_qty <= 0:
                raise ValidationError('La cantidad a cobrar debe ser mayor a cero.')

    @api.onchange('line_id')
    def _onchange_line(self):
        for m in self:
            if m.line_id and not m.contract_line_id:
                cl = m.contract_id.line_ids.filtered(lambda c: c.product_id == m.line_id.product_id and c.qty_remaining > 0)[:1]
                if cl:
                    m.contract_line_id = cl
                m.product_qty = m.line_id.product_qty
