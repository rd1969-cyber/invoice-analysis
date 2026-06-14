"""InXpress brand constants (from InXpress_Brand_Reference.docx, Mar 2026).

Edmonton & Atlantic Canada franchise. Used by the UI and report exports so the
app matches brand. Rules: sentence case only (never ALL CAPS / Title Case in
headlines), Inter font family, WCAG AA contrast.
"""
from __future__ import annotations

# Signature colours
MIDNIGHT_BLUE = "#022F65"   # primary brand colour
SPRING_GREEN = "#00D686"    # accent (never the primary logo colour)

# Primary colours (dynamics)
CORNSILK = "#FCF7DB"        # softens / humanises
VIVID_BLUE = "#0066FF"      # technical, vibrant
FOREST_GREEN = "#167979"    # positive, ethical

# Base
BLACK = "#000000"
WHITE = "#FFFFFF"

# Supporting palette (sparingly — charts, alerts)
RED = "#DB242E"
PURPLE = "#8F127D"
YELLOW = "#FFDA66"
SALMON = "#FFA487"
SILVER = "#B5B7BB"

# Semantic roles for this app
PRIMARY = MIDNIGHT_BLUE
ACCENT = SPRING_GREEN
COST_HIGH = RED            # my cost is HIGH / uncompetitive
COST_LOW = MIDNIGHT_BLUE   # competitive ("black"); margin highlighted in green
MARGIN_GOOD = FOREST_GREEN
SURFACE = "#F9F9F9"
SURFACE_BLUE = "#F0F4FA"

# Typography (Google Fonts: Inter + Inter Tight)
HEADLINE_FONT = "Inter Tight"
BODY_FONT = "Inter"
GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@600;700&display=swap"
)

CONTACT = {
    "business_name": "InXpress Edmonton & Atlantic Canada",
    "phone": "902 706 7666",
    "email": "platinumsupport@inxpress.com",
    "website": "https://ca.inxpress.com/locations/halifax-nova-scotia/",
}

APP_NAME = "Freight IQ"
APP_TAGLINE = "Invoice intelligence & rate comparison"
