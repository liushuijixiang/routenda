import os


TEST_ENV = {
    "OPENAI_API_KEY": "",
    "LLM_API_KEY": "",
    "CALENDAR_PROVIDER": "mock",
    "FEISHU_APP_ID": "",
    "FEISHU_APP_SECRET": "",
    "MICROSOFT_TENANT_ID": "",
    "MICROSOFT_CLIENT_ID": "",
    "MICROSOFT_CLIENT_SECRET": "",
    "ERP_PROVIDER": "mock",
    "ERP_NEXT_BASE_URL": "",
    "ERP_NEXT_API_KEY": "",
    "ERP_NEXT_API_SECRET": "",
    "SEARCH_PROVIDER": "disabled",
    "SERPER_API_KEY": "",
    "ROUTING_PROVIDER": "mock",
    "GEOCODING_PROVIDER": "mock",
}

for key, value in TEST_ENV.items():
    os.environ[key] = value
