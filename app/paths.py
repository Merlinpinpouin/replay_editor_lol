import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENTS = os.path.join(ROOT, "clients")
TOOLS = os.path.join(ROOT, "tools")
RENDERS = os.path.join(ROOT, "renders")
PROJECTS = os.path.join(ROOT, "projects")
REPLAYS = os.path.join(os.path.expanduser("~"), "Documents", "League of Legends", "Replays")
MANIFEST_DL = os.path.join(TOOLS, "ManifestDownloader.exe")
