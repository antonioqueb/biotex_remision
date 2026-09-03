from odoo import api, fields, models


class BiotexRemisionLine(models.Model):
    """Lo físicamente entregado."""
    _name = 'biotex.remision.line'
    _description = 'Producto entregado'
    _order = 'remision_id, sequence, id'

    remision_id = fields.Many2one('biotex.remision', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', required=True)
    product_qty = fields.Float(string='Cantidad', required=True, default=1.0)
    product_uom_id = fields.Many2one('uom.uom', string='Unidad', compute='_compute_uom', store=True, readonly=False)
    lot_name = fields.Char(string='Lote / caducidad', help='Informativo en fase 1 (R28 en fase posterior).')
    price_unit = fields.Float(string='Valor unitario (ref.)', digits='Product Price', help='Referencia: precio de la clave equivalente o lista.')
    currency_id = fields.Many2one(related='remision_id.currency_id')
    amount = fields.Monetary(compute='_compute_amount', store=True)
    mask_ids = fields.One2many('biotex.remision.mask', 'line_id', string='Claves con las que se cobra')
    masked_qty = fields.Float(compute='_compute_masked', string='Cubierto por claves')
    free_qty = fields.Float(compute='_compute_available', string='Disponible en delegación')

    @api.depends('product_id')
    def _compute_uom(self):
        for l in self:
            if l.product_id:
                l.product_uom_id = l.product_id.uom_id

    @api.depends('product_qty', 'price_unit')
    def _compute_amount(self):
        for l in self:
            l.amount = l.product_qty * l.price_unit

    @api.depends('mask_ids.product_qty')
    def _compute_masked(self):
        for l in self:
            l.masked_qty = sum(l.mask_ids.mapped('product_qty'))

    @api.depends('product_id', 'remision_id.warehouse_id')
    def _compute_available(self):
        for l in self:
            wh = l.remision_id.warehouse_id
            l.free_qty = l.product_id.sudo().with_context(warehouse_id=wh.id).free_qty if (l.product_id and wh) else 0.0

    @api.onchange('product_id')
    def _onchange_product(self):
        for l in self:
            if not l.product_id:
                continue
            contract = l.remision_id.contract_id
            cl = contract.line_ids.filtered(lambda c: c.product_id == l.product_id)[:1] if contract else False
            l.price_unit = cl.price_unit if cl else l.product_id.list_price
