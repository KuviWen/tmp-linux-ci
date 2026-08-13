from stock_forecasting.adapters.rest import create_web_app
from stock_forecasting.runtime import RuntimeSettings

app = create_web_app(RuntimeSettings.from_environment().build_application())
