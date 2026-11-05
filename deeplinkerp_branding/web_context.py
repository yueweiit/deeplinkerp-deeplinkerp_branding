BRAND_BASE_TEMPLATE = "deeplinkerp_branding/templates/deeplinkerp_branding_base.html"
BRAND_FAVICON = "/assets/deeplinkerp_branding/logo/erp_logo_rounded_64.png?v=0.0.9"
BRAND_SPLASH_IMAGE = "/assets/deeplinkerp_branding/logo/erp_logo_rounded_192.png?v=0.0.9"


def update_context(context):
	"""Use the Deeplinkerp base template where Frappe would use the default base."""
	values = {
		"favicon": BRAND_FAVICON,
		"splash_image": BRAND_SPLASH_IMAGE,
	}
	base_template_path = context.get("base_template_path")
	if base_template_path and base_template_path != "templates/base.html":
		return values

	values["base_template_path"] = BRAND_BASE_TEMPLATE
	return values
