from typing import Optional

def get_video_key(examination_alias:str, content:str, is_anonymous:bool=False) -> str:
    """
    Generates a video key based on the examination alias, content, and anonymity status.
    """
    if is_anonymous:
        return f"{examination_alias}-{content}-anonymous"
    else:
        return f"{examination_alias}-{content}-non_anonymous"
    
def identify_video_key(video_key:str) -> str:
    """
    Identifies the video key based on the provided string.
    """
    split_key = video_key.split("-")
    assert len(split_key) == 3, f"Invalid video key format: {video_key}"
    examination_alias = split_key[0]
    content = split_key[1]
    is_anonymous = split_key[2] == "anonymous"
    return examination_alias, content, is_anonymous


def get_video_key_regex_by_examination_alias(
    examination_alias:Optional[str]=None, content:Optional[str]=None, is_anonymous:Optional[bool]=None
):
    """
    Generates a regex pattern to match video keys based on examination alias, content, and anonymity status.
    If any of the parameters are None, they will be ignored in the regex.
    """
    pattern = ""
    if examination_alias:
        pattern += f"{examination_alias}-"
    if content:
        pattern += f"{content}-"
    if is_anonymous is not None:
        pattern += "anonymous" if is_anonymous else "non_anonymous"
    else:
        pattern += "(anonymous|non_anonymous)"
    
    return f"^{pattern}$"

