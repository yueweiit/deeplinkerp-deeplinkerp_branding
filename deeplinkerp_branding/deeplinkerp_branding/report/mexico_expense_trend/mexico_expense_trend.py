from deeplinkerp_branding.services.mexico_expenses import report_data


def execute(filters=None):
    return report_data(filters, kind="trend")
