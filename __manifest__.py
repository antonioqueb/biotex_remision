{
    'name': 'Remisiones con máscara de contrato',
    'summary': 'Remisión de salida por delegación con razón social del contrato, producto entregado vs. clave cobrada (máscara), consumo de contrato, firma y facturación agrupada',
    'version': '19.0.1.0.0',
    'category': 'Distribución de insumos',
    'author': 'Alphaqueb Consulting SAS',
    'license': 'LGPL-3',
    'icon': '/biotex_remision/static/description/icon.svg',
    'depends': ['biotex_contract', 'stock', 'account', 'sale_stock'],
    'data': [
        'security/ir.model.access.csv',
        'security/remision_security.xml',
        'data/remision_data.xml',
        'report/remision_report.xml',
        'views/remision_views.xml',
        'views/contract_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': ['biotex_remision/static/src/**/*'],
    },
    'installable': True,
}
