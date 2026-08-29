from __future__ import annotations


RESEARCH_SEED_GROUPS: dict[str, tuple[str, ...]] = {
    "Computing": (
        "Computer science", "Artificial intelligence", "Machine learning",
        "Cybersecurity", "Robotics", "Computer vision",
        "Natural language processing", "Data science",
        "Human-computer interaction", "Software engineering",
        "Distributed systems", "Information science",
    ),
    "Engineering": (
        "Electrical engineering", "Mechanical engineering", "Civil engineering",
        "Chemical engineering", "Aerospace engineering",
        "Biomedical engineering", "Environmental engineering",
        "Materials engineering", "Industrial engineering", "Nuclear engineering",
    ),
    "Life sciences": (
        "Biology", "Molecular biology", "Cell biology", "Genetics", "Genomics",
        "Microbiology", "Neuroscience", "Ecology", "Evolutionary biology",
        "Bioinformatics", "Biochemistry", "Biotechnology",
    ),
    "Health": (
        "Medicine", "Public health", "Epidemiology", "Nursing", "Pharmacy",
        "Immunology", "Cancer biology", "Mental health", "Nutrition",
        "Health informatics", "Kinesiology", "Dentistry",
    ),
    "Physical sciences": (
        "Physics", "Chemistry", "Mathematics", "Statistics", "Astronomy",
        "Earth science", "Atmospheric science", "Oceanography",
        "Geology", "Applied mathematics",
    ),
    "Social sciences": (
        "Political science", "Economics", "Sociology", "Psychology",
        "Anthropology", "Geography", "Communication studies", "Education",
        "Public policy", "Criminology", "International relations",
        "Social work", "Urban studies",
    ),
    "Humanities": (
        "History", "Philosophy", "Literature", "Linguistics",
        "Religious studies", "Art history", "Classics", "Archaeology",
        "Music studies", "Cultural studies", "Asian studies",
    ),
    "Business and law": (
        "Business administration", "Finance", "Accounting", "Marketing",
        "Management", "Entrepreneurship", "Legal studies",
        "Operations management", "Organizational behavior",
    ),
    "Agriculture and environment": (
        "Agricultural science", "Food science", "Forestry",
        "Veterinary medicine", "Environmental science", "Sustainability",
        "Conservation biology", "Climate science",
    ),
}


def seed_topic_names(groups: list[str] | None = None) -> list[str]:
    selected = groups or list(RESEARCH_SEED_GROUPS)
    unknown = [name for name in selected if name not in RESEARCH_SEED_GROUPS]
    if unknown:
        raise ValueError(f"Unknown seed group: {', '.join(unknown)}")
    return [
        topic
        for group in selected
        for topic in RESEARCH_SEED_GROUPS[group]
    ]
