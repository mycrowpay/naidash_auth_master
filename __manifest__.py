# -*- coding: utf-8 -*-
{
    'name': "NaiDash Auth",

    'summary': "NaiDash Registration & Authentication Services",

    'description': """
        NaiDash Registration & Authentication Services.
    """,

    'author': "NaiDash",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/17.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Naidash',
    'version': '0.1',

    # any module necessary for this one to work correctly
    'depends': ['base', 'contacts', 'auth_signup', 'mail'],

    # always loaded
    'data': [
        # 'security/groups.xml',
        # 'security/ir.model.access.csv',
        # 'data/ir_sequence_data.xml',
        'views/partner.xml',
        'views/settings.xml',
        'views/security_notification_template.xml'
    ],
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': True,
    'license': 'LGPL-3'
}
