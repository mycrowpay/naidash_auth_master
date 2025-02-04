from odoo import api, fields, models, _


class NaidashSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    app_1_base_url = fields.Char(string='Base URL For App No.1')

    def set_values(self):
        res = super(NaidashSettings, self).set_values()
        self.env['ir.config_parameter'].sudo().set_param('app_1_base_url', self.app_1_base_url)
        return res

    @api.model
    def get_values(self):
        res = super(NaidashSettings, self).get_values()
        base_url = self.env['ir.config_parameter'].sudo().get_param('app_1_base_url')
        res.update(
            { 
                'app_1_base_url': base_url
            }
        )

        return res 