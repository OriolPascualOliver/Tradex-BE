import os


# Ensure that the application has a SECRET_KEY during tests. In production this
# must be set through a proper secret management mechanism.
os.environ.setdefault("SECRET_KEY", "test-secret-key")

