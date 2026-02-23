"""Shared test helpers for functional tests.

Utilities used across multiple functional test files.
"""

from html.parser import HTMLParser


class FormFieldCollector(HTMLParser):
    """Collect form field names and values from rendered HTML.

    Used to implement Issue #5 round-trip testing pattern: extract
    field names/values from a GET response, then POST them back to
    verify the form→view contract.

    Usage::

        parser = FormFieldCollector()
        parser.feed(response.content.decode())
        fields = parser.fields  # dict of name -> value
    """

    def __init__(self):
        super().__init__()
        self.fields = {}
        self._current_select = None
        self._current_options = []
        self._in_textarea = None
        self._textarea_content = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "input":
            name = attrs_dict.get("name")
            if name:
                self.fields[name] = attrs_dict.get("value", "")
        elif tag == "select":
            self._current_select = attrs_dict.get("name")
            self._current_options = []
        elif tag == "option" and self._current_select:
            val = attrs_dict.get("value", "")
            if val:
                self._current_options.append(val)
            if "selected" in attrs_dict:
                self.fields[self._current_select] = val
        elif tag == "textarea":
            self._in_textarea = attrs_dict.get("name")
            self._textarea_content = []

    def handle_data(self, data):
        if self._in_textarea is not None:
            self._textarea_content.append(data)

    def handle_endtag(self, tag):
        if tag == "select" and self._current_select:
            if (
                self._current_select not in self.fields
                and self._current_options
            ):
                self.fields[self._current_select] = self._current_options[0]
            self._current_select = None
        elif tag == "textarea" and self._in_textarea:
            self.fields[self._in_textarea] = "".join(self._textarea_content)
            self._in_textarea = None
