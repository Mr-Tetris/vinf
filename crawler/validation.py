"""Recognize unusable HTTP-200 bodies, including the site's content preference gate."""

import re


def blocked_document(raw):
    text = raw.decode('utf-8', errors='replace')
    if re.search(r'/cdn-cgi/challenge-platform/|\bcf-chl-|id\s*=\s*[\'"]challenge-form[\'"]', text, re.I):
        return 'challenge_page'
    title = re.search(r'<title\b[^>]*>(.*?)</title\s*>', text, re.I | re.S)
    if title and re.search(r'just a moment|attention required|access denied|checking your browser|verify you are human', title[1], re.I):
        return 'challenge_page'
    cleaned = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    if re.search(r'<h[1-6]\b[^>]*>\s*Filtered Content\s*</h[1-6]\s*>', cleaned, re.I):
        return 'content_filtered'
    return ''
