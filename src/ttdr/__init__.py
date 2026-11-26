import warnings

# urllib3 warns when its SOCKS support is imported without PySocks installed; ttdr only
# reaches localhost and the Hub, so the warning is noise on every command.
warnings.filterwarnings("ignore", message="SOCKS support in urllib3")
