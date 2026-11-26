from dataclasses import dataclass

from codec_fixtures.shapes import Circle

from ._hidden import Hidden


@dataclass
class Ring:
    inner: Circle
