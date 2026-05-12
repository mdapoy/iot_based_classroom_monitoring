import warnings

# Suppress ALL warnings at interpreter startup.
#
# The targeted filterwarnings("ignore", category=UserWarning, ...) filter was
# not catching the Pydantic ArbitraryTypeWarning in time — the warning is
# emitted (and logged as an error) by google-genai / Google libraries during
# the import phase, before the filter could take effect.
#
# Using simplefilter("ignore") here ensures every warning is silenced from the
# very first import, allowing the FastAPI app to start cleanly.
#
# TODO: Once the app is stable, narrow this back to a targeted filter and
#       address the root cause (google-genai using `any` as a type annotation).
warnings.simplefilter("ignore")
