"""Whether an AI answer needs pictures at all, and what to search for when it does.

Images are decided before any model call, so the decision is free and the gallery can show
before the text. The answer model later gets the retrieved titles and may prune or hide them
(`ai_service`), but it never adds pictures this step didn't find.
"""
import re

# Asked for in so many words: the answer is the pictures.
EXPLICIT = re.compile(r"\b(images?|photos?|photographs?|pictures?|pics?|wallpapers?|show me|looks? like|appearance|"
                      r"diagrams?|videos?|trailers?|footage|clips?)\b", re.I)
VIDEO = re.compile(r"\b(videos?|trailers?|footage|clips?|tutorial|demo|how to (?:tie|fold|knit|draw|install|replace))\b", re.I)

# Numbers, forecasts, code and money: a photo would only be decoration.
ABSTRACT = re.compile(
    r"\b(loan|emi|interest rates?|mortgage|calculat\w*|convert|exchange rate|percent|forecast|weather|time in|timezone|"
    r"tax(?:es)?|salary|invest\w*|stocks?|shares|crypto|inflation|budget|insurance|"
    r"python|javascript|typescript|java|rust|golang|sql|regex|api|function|error|exception|bug|code|compile|"
    r"install|command|terminal|git|docker|npm|excel|formula|equation|proof|algorithm|"
    r"meaning|mean|means|definition|define|synonyms?|grammar|spell|translate|quote|lyrics|"
    r"law|legal|policy|visa|deadline|schedule)\b|\d\s*%|\d\s*[*/+^x-]\s*\d", re.I)

# Things people recognise by sight: species, places, objects, food, art, the sky.
CONCRETE = re.compile(
    r"\b(animals?|dogs?|cats?|birds?|fish|insects?|spiders?|snakes?|sharks?|whales?|horses?|breeds?|species|"
    r"plants?|flowers?|trees?|mushrooms?|dinosaurs?|fossils?|minerals?|gems?|crystals?|"
    r"cit(?:y|ies)|islands?|mountains?|volcano(?:es)?|lakes?|beach(?:es)?|waterfalls?|national park|landmarks?|"
    r"monuments?|temples?|castles?|cathedrals?|bridges?|buildings?|skyscrapers?|architecture|museums?|"
    r"phones?|smartphones?|iphone|pixel|galaxy s\d+|laptops?|macbook|cameras?|lens(?:es)?|headphones?|earbuds?|"
    r"speakers?|tablets?|ipad|monitors?|tvs?|smartwatch(?:es)?|keyboards?|consoles?|gpus?|drones?|"
    r"cars?|suvs?|trucks?|bikes?|bicycles?|motorcycles?|vehicles?|aircraft|planes?|jets?|ships?|rockets?|"
    r"recipes?|dish(?:es)?|cakes?|bread|cuisine|cocktails?|"
    r"paintings?|sculptures?|artworks?|murals?|fashion|dress(?:es)?|shoes|sneakers|outfits?|hairstyles?|tattoos?|"
    r"furniture|interior|gardens?|planets?|galax(?:y|ies)|nebulae?|eclipse|aurora|anatomy|skeleton)\b", re.I)
PRODUCT_EVIDENCE = re.compile(r"\b(device|smart display|speaker|phone|laptop|camera|headphones|vehicle|model year)\b", re.I)

LEAD = re.compile(r"^(?:(?:can|could) you |please )?(?:show|find|give|get)(?: me)?\s+|"
                  r"^(?:tell me (?:about|more about)|explain|describe)\s+|"
                  r"^(?:what|who|where|which|why|how|when)(?:'s| is| are| was| were| does| do| did| can| could| should)?\s+", re.I)
FILLER = re.compile(r"\b(?:some|more|images?|photos?|photographs?|pictures?|pics?|videos?|of|for|the|a|an|it|this|that|"
                    r"please|look|looks|like|work|works|appearance)\b", re.I)


def image_query(question: str, subject: str = "") -> str:
    """The thing to picture: the question minus its scaffolding, or the original search if little is left."""
    def clean(text):
        text = LEAD.sub("", text.strip())
        text = LEAD.sub("", text)  # "can you show me what a quokka looks like"
        return " ".join(FILLER.sub(" ", text).split()).strip(" ?.!")
    query = clean(question)
    if len(query) < 4 and subject:
        query = clean(subject) or subject
    return (query or question)[:200]


def plan(question: str, subject: str = "", evidence=(), entity: bool = False, followup: bool = False,
         comparison: bool = False) -> dict:
    """{"need": required|helpful|none, "category", "query", "reason"} for one AI turn.

    required: the user asked for pictures. helpful: the subject is something people recognise by
    sight. none: everything else, including follow-ups that didn't ask, so images don't repeat.
    """
    category = "videos" if VIDEO.search(question) else "images"
    query = image_query(question, subject)
    if comparison:
        return {"need": "none", "category": category, "query": query, "reason": "Comparing selected sources."}
    if EXPLICIT.search(question):
        return {"need": "required", "category": category, "query": query, "reason": "You asked for visuals."}
    text = question if followup else question + " " + subject
    if followup or ABSTRACT.search(text):
        return {"need": "none", "category": category, "query": query,
                "reason": "Follow-up without a visual request." if followup else "Pictures wouldn't add to this answer."}
    short_product = len(question.split()) <= 5 and any(PRODUCT_EVIDENCE.search(getattr(e, "text", "")) for e in list(evidence)[:4])
    if CONCRETE.search(text) or entity or short_product:
        return {"need": "helpful", "category": "images", "query": query, "reason": "A visual subject."}
    return {"need": "none", "category": category, "query": query, "reason": "Pictures wouldn't add to this answer."}
