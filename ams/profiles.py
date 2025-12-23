# Task Profiles for AMS
# Defines which artefacts (by their types) are relevant to each profile.

PROFILES = {
    "frontend": {
        "relevant_artefacts": ["html", "css", "js"]
    },
    "fullstack": {
        "relevant_artefacts": ["html", "css", "js", "php", "sql"]
    },
}


def get_profile(profile_name):
    """Retrieve the artefact types relevant for the given profile name."""
    try:
        return PROFILES[profile_name]["relevant_artefacts"]
    except KeyError:
        raise ValueError(f"Unknown profile: {profile_name}")

