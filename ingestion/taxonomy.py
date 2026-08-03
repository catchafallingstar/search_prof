# ingestion/taxonomy.py
import requests
import urllib.parse
from typing import Dict, Any

# ==============================================================================
# LAYER 2: THE 26-TO-6 AGENCY ROUTER MATRIX
# Maps the 26 standard OpenAlex fields to 6 federal funding agency strategies
# ==============================================================================
AGENCY_MATRIX = {
    "CISE_NSF": {
        "fields": [
            "Computer Science", "Decision Sciences"
        ],
        "primary_agency": "NSF CISE",
        "programs": ["CRII", "CAREER"],
        "strategy": "NSF_CISE_SEARCH"
    },
    "NIH_BIOMED": {
        "fields": [
            "Medicine", "Biochemistry, Genetics and Molecular Biology", "Neuroscience", 
            "Nursing", "Pharmacology, Toxicology and Pharmaceutics", "Health Professions", 
            "Immunology and Microbiology", "Dentistry"
        ],
        "primary_agency": "NIH",
        "programs": ["K99/R00", "Early Stage Investigator (ESI)", "R01"],
        "strategy": "NIH_REPORTER_SEARCH"
    },
    "NSF_ENG_MPS_DOD": {
        "fields": [
            "Engineering", "Chemical Engineering", "Materials Science", 
            "Energy", "Physics and Astronomy", "Chemistry"
        ],
        "primary_agency": "NSF ENG/MPS & DoD YIP",
        "programs": ["NSF CAREER", "NSF CRII", "DoD YIP", "ONR/AFOSR"],
        "strategy": "NSF_ENG_DOD_SEARCH"
    },
    "NSF_SBE": {
        "fields": [
            "Economics, Econometrics and Finance", "Business, Management and Accounting", 
            "Social Sciences", "Psychology"
        ],
        "primary_agency": "NSF SBE",
        "programs": ["NSF CAREER (SBE Directorate)", "NLF"],
        "strategy": "NSF_SBE_SEARCH"
    },
    "NSF_GEO_BIO_USDA": {
        "fields": [
            "Earth and Planetary Sciences", "Environmental Science", 
            "Agricultural and Biological Sciences", "Veterinary Science and Veterinary Medicine"
        ],
        "primary_agency": "NSF GEO/BIO & USDA",
        "programs": ["NSF CAREER (GEO/BIO)", "USDA NIFA AFRI"],
        "strategy": "NSF_GEO_USDA_SEARCH"
    },
    "NEH_HUMANITIES": {
        "fields": [
            "Arts and Humanities"
        ],
        "primary_agency": "NEH",
        "programs": ["NEH Fellowships", "NEH Summer Stipends"],
        "strategy": "NEH_HUMANITIES_SEARCH"
    }
}

OPENALEX_TOPICS_URL = "https://api.openalex.org/topics"

def normalize_taxonomy(raw_query: str) -> Dict[str, Any]:
    """
    LAYER 1: Zero-hardcoding taxonomy lookup via OpenAlex Topics API.
    Resolves raw query (e.g. "bme", "Bio Engr", "Quantum Computing") into 
    canonical Topic, Field (26 total), Domain (4 total), and Keywords.
    """
    clean_query = raw_query.strip()
    encoded_query = urllib.parse.quote(clean_query)
    
    default_result = {
        "raw_query": clean_query,
        "topic_id": None,
        "topic_name": clean_query,
        "field_name": "Engineering",  # Fallback default
        "domain_name": "Physical Sciences",
        "keywords": [clean_query],
        "agency_category": "NSF_ENG_MPS_DOD",
        "router_config": AGENCY_MATRIX["NSF_ENG_MPS_DOD"]
    }

    try:
        url = f"{OPENALEX_TOPICS_URL}?search={encoded_query}&per_page=1"
        res = requests.get(url, timeout=6)
        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])
            
            if results:
                top_hit = results[0]
                topic_id = top_hit.get("id")
                topic_name = top_hit.get("display_name", clean_query)
                field_obj = top_hit.get("field", {})
                domain_obj = top_hit.get("domain", {})
                
                field_name = field_obj.get("display_name", "Engineering")
                domain_name = domain_obj.get("display_name", "Physical Sciences")
                keywords = top_hit.get("keywords", [clean_query])

                # Route to 1 of 6 agency categories using field matching
                matched_category = "NSF_ENG_MPS_DOD" # fallback
                for cat, config in AGENCY_MATRIX.items():
                    if any(f.lower() in field_name.lower() or field_name.lower() in f.lower() for f in config["fields"]):
                        matched_category = cat
                        break

                return {
                    "raw_query": clean_query,
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "field_name": field_name,
                    "domain_name": domain_name,
                    "keywords": keywords if keywords else [clean_query],
                    "agency_category": matched_category,
                    "router_config": AGENCY_MATRIX[matched_category]
                }
    except Exception as e:
        print(f"⚠️ OpenAlex Taxonomy normalization warning: {e}")

    return default_result