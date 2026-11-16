from frappe.model.document import Document


class MexicoExpenseSettings(Document):
    def validate(self):
        from deeplinkerp_branding.services.mexico_expenses import validate_managed_document
        validate_managed_document(self)

    on_trash = validate

    def before_rename(self, *args, **kwargs):
        self.validate()
