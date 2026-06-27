{
    'name': 'Partner Matching Tool',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'View partner bills and invoices side-by-side with advanced filters for quick accounting reconciliation',
    'description': """
        Partner Matching Tool for Odoo Accounting
        =========================================
        This module provides a unified view to compare partner bills, customer invoices, and vendor bills side-by-side. 
        It significantly speeds up the financial reconciliation and payment matching process by offering robust filters 
        and a clean layout tailored for accountants and financial auditors using Odoo 17.
        
        Key Features:
        * Side-by-side comparison of partner accounting entries.
        * Advanced filtering for vendor bills, receipts, and customer invoices.
        * Streamlined workflow for financial reconciliation in Odoo.
    """,
    'author': 'Concept Solutions LLC',
    'website': 'https://www.csloman.com',
    'support': 'info@csloman.com',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/partner_matching_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
