# This file is part magento_product module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
from datetime import datetime
from decimal import Decimal
from creole import creole2html
from io import BytesIO
from trytond.model import ModelView, ModelSQL, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval, Not, Equal, Or
from trytond.transaction import Transaction
from trytond import backend
from trytond.tools import grouped_slice
from trytond.config import config as config_
from trytond.modules.product_esale.tools import esale_eval, slugify, unaccent
import unicodecsv

__all__ = ['MagentoProductType', 'MagentoAttributeConfigurable',
    'TemplateMagentoAttributeConfigurable', 'Template', 'Product']

MAX_CSV = config_.getint('magento', 'max_csv', default=50)
_MAGENTO_VISIBILITY = {
    'none': '1',
    'catalog': '2',
    'search': '3',
    'all': '4',
    }


class MagentoProductType(ModelSQL, ModelView):
    'Magento Product Type'
    __name__ = 'magento.product.type'
    name = fields.Char('Name', required=True, translate=True)
    code = fields.Char('Code', required=True,
        help='Same name Magento product type, (example: simple)')
    active = fields.Boolean('Active')

    @staticmethod
    def default_active():
        return True


class MagentoAttributeConfigurable(ModelSQL, ModelView):
    'Magento Attribute Configurable'
    __name__ = 'magento.attribute.configurable'
    app = fields.Many2One('magento.app', 'APP', required=True)
    name = fields.Char('Name', required=True, translate=True)
    code = fields.Char('Code', required=True)
    mgn_id = fields.Char('Mgn ID', required=True,
        help='Magento ID')
    active = fields.Boolean('Active')

    @staticmethod
    def default_active():
        return True

    @staticmethod
    def default_app():
        App = Pool().get('magento.app')
        apps = App.search([])
        if len(apps) == 1:
            return apps[0].id


class TemplateMagentoAttributeConfigurable(ModelSQL):
    'Product Template - Magento Attribute Configurable'
    __name__ = 'product.template-magento.attribute.configurable'
    _table = 'product_tpl_mgn_attribute_configurable'
    template = fields.Many2One('product.template', 'Template', ondelete='CASCADE',
            required=True, select=True)
    configurable = fields.Many2One('magento.attribute.configurable', 'Attribute Configurable',
        ondelete='CASCADE', required=True, select=True)

    @classmethod
    def __register__(cls, module_name):

        # Migration from 3.6: rename table
        old_table = 'product_template_magento_attribute_configurable_rel'
        new_table = 'product_tpl_mgn_attribute_configurable'
        if backend.TableHandler.table_exist(old_table):
            backend.TableHandler.table_rename(old_table, new_table)

        super(TemplateMagentoAttributeConfigurable, cls).__register__(module_name)


class Template(metaclass=PoolMeta):
    __name__ = 'product.template'
    magento_product_type = fields.Selection('get_magento_product_type', 'Product Type',
        states={
            'required': Eval('esale_available', True),
        },
        depends=['esale_available'])
    magento_group_price = fields.Boolean('Magento Grup Price',
        help='If check this value, when export product prices (and shop '
            'is active group price), export prices by group')
    magento_attribute_configurables = fields.Many2Many('product.template-magento.attribute.configurable',
        'template', 'configurable', 'Configurable Attribute', states={
            'invisible': ~(Eval('magento_product_type') == 'configurable'),
            'required': Eval('magento_product_type') == 'configurable',
            },
        depends=['magento_product_type'],
        help='Add attributes before export configurable product')

    @classmethod
    def __setup__(cls):
        super(Template, cls).__setup__()
        # Add base code require attribute
        for fname in ('code',):
            fstates = getattr(cls, fname).states
            if fstates.get('required'):
                fstates['required'] = Or(fstates['required'],
                    Eval('magento_product_type') == 'configurable')
            else:
                fstates['required'] = Eval('magento_product_type') == 'configurable'
            getattr(cls, fname).depends.append('magento_product_type')

    @classmethod
    def view_attributes(cls):
        return super(Template, cls).view_attributes() + [
            ('//page[@id="magento-attribute-configurables"]', 'states', {
                    'invisible': Not(Equal(Eval('magento_product_type'), 'configurable')),
                    })]

    @classmethod
    def get_magento_product_type(cls):
        ProductType = Pool().get('magento.product.type')

        types = [(None, '')]
        for type_ in ProductType.search([
                ('active', '=', True)], order=[('id', 'DESC')]):
            types.append((type_.code, type_.name))
        return types

    @staticmethod
    def default_magento_product_type():
        product_type = None
        ProductType = Pool().get('magento.product.type')
        ids = ProductType.search([
            ('code', '=', 'simple'),
            ('active', '=', True),
            ])
        if len(ids)>0:
            product_type = 'simple'
        return product_type


class Product(metaclass=PoolMeta):
    __name__ = 'product.product'

    @classmethod
    def get_magento_product_type(cls):
        Template = Pool().get('product.template')
        return Template.get_magento_product_type()

    @classmethod
    def magento_import_product(cls, values, shop=None):
        '''Magento Import Product values'''
        vals = super(Product, cls).magento_import_product(values, shop)

        visibility = values.get('visibility')
        if visibility == '1':
            visibility = 'none'
        elif visibility == '2':
            visibility = 'catalog'
        elif visibility == '3':
            visibility = 'search'
        else:
            visibility = 'all'

        status = values.get('status', '1')
        if status == '1':
            esale_available = True
        else:
            esale_available = False

        vals['esale_available'] = True
        vals['esale_active'] = True
        vals['esale_shortdescription'] = values.get('short_description')
        vals['esale_slug'] = values.get('url_key')
        vals['magento_product_type'] = values.get('type_id')
        vals['template_attributes'] = {'tax_class_id': values.get('tax_class_id', '0')}
        vals['esale_visibility'] = visibility
        vals['esale_attribute_group'] = 1 # ID default attribute
        vals['esale_available'] = esale_available
        vals['esale_shortdescription'] = values.get('short_description')
        vals['esale_metadescription'] = values.get('meta_description')
        vals['esale_metakeyword'] = values.get('meta_keyword')
        vals['esale_metatitle'] = values.get('meta_title')
        vals['esale_description'] = values.get('description')
        if values.get('special_price'):
            vals['special_price'] = Decimal(values['special_price'])
            if values.get('special_from_date'):
                vals['special_price_from'] = datetime.strptime(values['special_from_date'], "%Y-%m-%d %H:%M:%S")
            if values.get('special_to_date'):
                vals['special_price_to'] = datetime.strptime(values['special_to_date'], "%Y-%m-%d %H:%M:%S")
        return vals

    @classmethod
    def magento_export_product(cls, app, product, shop=None, lang='en_US'):
        '''Magento Export Product values'''
        pool = Pool()
        MagentoExternalReferential = pool.get('magento.external.referential')
        Product = pool.get('product.product')

        wikimarkup = app.wikimarkup

        language = Transaction().context.get('language')
        if language != lang:
            with Transaction().set_context(language=lang):
                product = Product(product.id)

        tax_class_id = ''
        if product.template.template_attributes:
            tax_class_id = product.template.template_attributes.get('tax_class_id', '')
        if product.attributes:
            if product.attributes.get('tax_class_id'):
                tax_class_id = product.attributes.get('tax_class_id', '')

        vals = {}
        vals['name'] = product.name
        vals['sku'] = product.code
        vals['type_id'] = product.magento_product_type
        vals['url_key'] = product.esale_slug if product.esale_slug else product.template.esale_slug
        vals['cost'] = str(product.cost_price)
        if shop:
            prices = shop.magento_get_prices(product)
            vals.update(prices)
        else:
            vals['price'] = str(product.list_price)
        vals['tax_class_id'] = tax_class_id
        vals['visibility'] = _MAGENTO_VISIBILITY.get(product.esale_visibility, '4')
        vals['set'] = '4' #ID default attribute
        vals['status'] = '1' if product.esale_active else '2'
        short_description = esale_eval(product.esale_shortdescription, product)
        vals['short_description'] = (creole2html(short_description) \
                if wikimarkup else short_description) if short_description else ''
        vals['meta_description'] = esale_eval(product.esale_metadescription, product)
        vals['meta_keyword'] = esale_eval(product.esale_metakeyword, product)
        vals['meta_title'] = esale_eval(product.esale_metatitle, product)
        description = esale_eval(product.esale_description, product)
        vals['description'] = (creole2html(description) \
                if wikimarkup else description) if description else ''
        vals['categories'] = [menu.magento_id for menu in product.esale_menus
                if menu.magento_app == app]

        websites = []
        for shop in product.shops:
            ext_ref = MagentoExternalReferential.get_try2mgn(app,
                    'magento.website',
                    shop.magento_website.id)
            if ext_ref:
                websites.append(ext_ref.mgn_id)
        vals['websites'] = websites
        return vals

    @classmethod
    def magento_export_product_configurable(cls, app, template, shop=None, lang='en_US'):
        '''Magento Export Configurable Product values (template)'''
        pool = Pool()
        MagentoExternalReferential = pool.get('magento.external.referential')
        Template = pool.get('product.template')

        wikimarkup = app.wikimarkup

        language = Transaction().context.get('language')
        if language != lang:
            with Transaction().set_context(language=lang):
                template = Template(template.id)

        vals = {}
        vals['name'] = template.name
        vals['sku'] = template.code
        vals['url_key'] = template.esale_slug
        vals['cost'] = str(template.cost_price)
        vals['price'] = str(template.list_price)
        vals['tax_class_id'] = template.attributes.get('tax_class_id') if template.attributes else None
        vals['visibility'] = _MAGENTO_VISIBILITY.get(template.esale_visibility, '4')
        vals['set'] = '4' #ID default attribute
        vals['status'] = '1' if template.esale_active else '2'
        short_description = esale_eval(template.esale_shortdescription, template)
        vals['short_description'] = (creole2html(short_description) \
                if wikimarkup else short_description) if short_description else ''
        vals['meta_description'] = esale_eval(template.esale_metadescription, template)
        vals['meta_keyword'] = esale_eval(template.esale_metakeyword, template)
        vals['meta_title'] = esale_eval(template.esale_metatitle, template)
        description = esale_eval(template.esale_description, template)
        vals['description'] = (creole2html(description) \
                if wikimarkup else description) if description else ''
        vals['categories'] = [menu.magento_id for menu in template.esale_menus if menu.magento_app == app]

        websites = []
        for shop in template.shops:
            ext_ref = MagentoExternalReferential.get_try2mgn(app,
                    'magento.website',
                    shop.magento_website.id)
            if ext_ref:
                websites.append(ext_ref.mgn_id)
        vals['websites'] = websites
        return vals

    @classmethod
    def esale_export_csv_magento(cls, shop, products, lang):
        Product = Pool().get('product.product')

        values, keys = [], set()
        if products:
            app = shop.magento_website.magento_app

            context = Transaction().context
            context['shop'] = shop.id
            with Transaction().set_context(context):
                quantities = shop.get_esale_product_quantity(products)

            for sub_products in grouped_slice(products, MAX_CSV):
                for product in sub_products:
                    quantity = quantities[product.id]
                    vals = Product.magento_export_product_csv(
                        app, product, shop, lang, quantity)
                    for k in vals.keys():
                        keys.add(k)
                    values.append(vals)

        output = BytesIO()
        wr = unicodecsv.DictWriter(output, sorted(list(keys)),
            quoting=unicodecsv.QUOTE_ALL, encoding='utf-8')
        wr.writeheader()
        wr.writerows(values)
        return output

    @classmethod
    def magento_export_product_csv(cls, app, product, shop, lang, quantity):
        Configuration = Pool().get('product.configuration')
        configuration = Configuration(1)

        vals = cls.magento_export_product(app, product, shop, lang)

        if app.default_lang and (app.default_lang.code == lang):
            # remove websites
            if vals.get('websites'):
                del vals['websites']

            # convert list values to string
            vals['category_ids'] = ', '.join(str(x) for x in vals.get('categories'))
            del vals['categories']

            vals['qty'] = quantity
            vals['is_in_stock'] = '1' if quantity > 0 else '0'
            vals['manage_stock'] = '1' if product.esale_manage_stock else '0'
            if (hasattr(shop, 'magento_use_config_manage_stock')
                    and shop.magento_use_config_manage_stock):
                vals['use_config_manage_stock'] = ('1'
                    if product.magento_use_config_manage_stock else '0')

            # images
            # http://wiki.magmi.org/index.php?title=Image_attributes_processor
            image = None
            small_image = None
            thumbnail = None
            media_gallery =  []
            for a in product.template.attachments:
                if not a.esale_available:
                    continue
                img = '%(exclude)s%(uri)s%(digest)s/%(filename)s::%(label)s' % {
                    'exclude': '-' if a.esale_exclude else '',
                    'uri': configuration.esale_media_uri,
                    'digest': a.digest,
                    'filename': slugify(a.name),
                    'label': unaccent( a.description if a.description \
                        else product.template.name),
                    }
                if a.esale_base_image and not image:
                    image = img
                if a.esale_small_image and not small_image:
                    small_image = img
                if a.esale_thumbnail and not thumbnail:
                    thumbnail = img
                media_gallery.append(img)
            vals['image'] = image or ''
            vals['small_image'] = small_image or ''
            vals['thumbnail'] = thumbnail or ''
            vals['media_gallery'] = ';'.join(media_gallery)
        else:
            # storeview
            for l in app.languages:
                if lang == l.lang.code:
                    vals['store'] = l.storeview.code
                    break
        return vals
