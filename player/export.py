"""PDF export for setlists using ReportLab."""

import io
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_DIR = os.path.dirname(__file__)
ICON_DIR = os.path.join(_DIR, 'export_icons')
FONT_DIR = os.path.join(_DIR, 'export_fonts')
ICON_H = 30

_fonts_registered = False


def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont('Roboto', os.path.join(FONT_DIR, 'Roboto-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('Roboto-Bold', os.path.join(FONT_DIR, 'Roboto-Bold.ttf')))
    pdfmetrics.registerFont(TTFont('RobotoCond', os.path.join(FONT_DIR, 'RobotoCondensed-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('RobotoCond-Bold', os.path.join(FONT_DIR, 'RobotoCondensed-Bold.ttf')))
    pdfmetrics.registerFontFamily('Roboto', normal='Roboto', bold='Roboto-Bold')
    pdfmetrics.registerFontFamily('RobotoCond', normal='RobotoCond', bold='RobotoCond-Bold')
    _fonts_registered = True


def _icon_path(name):
    return os.path.join(ICON_DIR, name)


class IconRow(Flowable):
    """A flowable that renders a horizontal row of inline images."""

    def __init__(self, image_paths, icon_h=ICON_H, gap=4):
        super().__init__()
        self.image_paths = image_paths
        self.icon_h = icon_h
        self.gap = gap
        self.width = len(image_paths) * icon_h + max(0, len(image_paths) - 1) * gap
        self.height = icon_h

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        x = 0
        for path in self.image_paths:
            self.canv.drawImage(
                path, x, 0, self.icon_h, self.icon_h,
                preserveAspectRatio=True, mask='auto',
            )
            x += self.icon_h + self.gap


def _build_icon_paths(info):
    """Build a list of icon file paths from info.json guitar data."""
    if not info or not info.get('guitars'):
        return []

    guitars = info['guitars']
    paths = []
    has_detuning = False
    has_drop_d = False
    has_capo = False

    for key in ('lead_guitar', 'rhythm_guitar'):
        g = guitars.get(key)
        if not g:
            continue
        gtype = (g.get('type') or '').lower()
        if gtype == 'electric':
            paths.append(_icon_path('electric.png'))
        elif gtype == 'acoustic':
            paths.append(_icon_path('acoustic.png'))

        tuning = g.get('tuning', 0)
        if isinstance(tuning, str):
            if tuning.lower() == 'drop d':
                has_drop_d = True
            elif tuning.lower() not in ('', 'standard'):
                has_detuning = True
        elif isinstance(tuning, (int, float)) and tuning != 0:
            has_detuning = True

        if g.get('capo', 0):
            has_capo = True

    bass = guitars.get('bass_guitar')
    if bass:
        tuning = bass.get('tuning', 0)
        if isinstance(tuning, str):
            if tuning.lower() == 'drop d':
                has_drop_d = True
            elif tuning.lower() not in ('', 'standard'):
                has_detuning = True
        elif isinstance(tuning, (int, float)) and tuning != 0:
            has_detuning = True

    if has_detuning:
        paths.append(_icon_path('down_arrow.png'))
    if has_drop_d:
        paths.append(_icon_path('drop_d.png'))
    if has_capo:
        paths.append(_icon_path('capo.png'))

    return paths


def _starter_initial(info):
    """Get the first character of the 'starts' field."""
    if not info:
        return ''
    starts = info.get('starts', '')
    return starts[0].upper() if starts else ''


def _song_title(song_entry):
    """Get the display title for a song entry."""
    info = song_entry.get('info')
    if info and info.get('title'):
        return info['title']
    return song_entry.get('song_name', '')


def _split_into_sets(song_data):
    """Split song_data list into sets, using breaks as dividers."""
    sets = []
    current = []
    for item in song_data:
        if item.get('is_break'):
            if current:
                sets.append(current)
                current = []
        else:
            current.append(item)
    if current:
        sets.append(current)
    return sets


def render_setlist_pdf(setlist, song_data):
    """Render a setlist to a PDF byte buffer.

    Returns an io.BytesIO positioned at 0, ready to be served.
    """
    _register_fonts()

    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    title_style = ParagraphStyle(
        'SetlistTitle',
        fontName='Roboto-Bold',
        fontSize=34,
        leading=42,
        spaceAfter=12,
        alignment=1,
    )

    song_style = ParagraphStyle(
        'SongName',
        fontName='RobotoCond',
        fontSize=26,
        leading=32,
    )

    starter_style = ParagraphStyle(
        'Starter',
        fontName='RobotoCond-Bold',
        fontSize=26,
        leading=32,
        alignment=1,
    )

    date_str = ''
    if setlist.date:
        date_str = setlist.date.strftime('%-m/%-d/%Y')

    page_title = setlist.name
    if date_str:
        page_title = f'{setlist.name} - {date_str}'

    sets = _split_into_sets(song_data)
    if not sets:
        sets = [[]]

    break_style = ParagraphStyle(
        'Break',
        fontName='RobotoCond-Bold',
        fontSize=26,
        leading=32,
        alignment=0,
    )

    story = []

    for set_idx, song_set in enumerate(sets):
        if set_idx > 0:
            story.append(PageBreak())

        story.append(Paragraph(page_title, title_style))
        story.append(Spacer(1, 6))

        if not song_set:
            story.append(Paragraph('(empty set)', song_style))
            continue

        table_data = []
        for song_entry in song_set:
            title = _song_title(song_entry)
            icon_paths = _build_icon_paths(song_entry.get('info'))
            starter = _starter_initial(song_entry.get('info'))

            icon_cell = IconRow(icon_paths) if icon_paths else ''

            table_data.append([
                Paragraph(title, song_style),
                icon_cell,
                Paragraph(starter, starter_style),
            ])

        is_last_set = set_idx == len(sets) - 1
        if not is_last_set:
            table_data.append([
                Paragraph('(Break)', break_style),
                '',
                '',
            ])

        page_width = letter[0] - 1.0 * inch
        col_widths = [
            page_width * 0.62,
            page_width * 0.28,
            page_width * 0.10,
        ]

        row_height = 42
        table = Table(table_data, colWidths=col_widths, rowHeights=row_height)
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (0, -1), 10),
            ('LEFTPADDING', (1, 0), (1, -1), 6),
            ('RIGHTPADDING', (-1, 0), (-1, -1), 10),
            ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.Color(0.78, 0.78, 0.78)),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.Color(0.5, 0.5, 0.5)),
        ]))

        story.append(table)

    doc.build(story)
    buf.seek(0)
    return buf
