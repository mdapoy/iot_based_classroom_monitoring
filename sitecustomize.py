import warnings

# Suppress Pydantic's ArbitraryTypeWarning raised by third-party dependencies
# (e.g. google-genai) that use built-in types such as `any` as type annotations.
# This file is loaded by the Python interpreter before any other imports, making
# it the only reliable place to catch warnings emitted during the import phase.
warnings.filterwarnings("ignore", category=UserWarning, message=".*ArbitraryType.*")
