"""Deterministic PDF outputs from saved answers. No inferred facts or sample data."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether)

from services.home_facts.schema import ROOT, manifest, fields, display_value

WIDTH = 525.6
INK = colors.HexColor('#101113')
TEAL = colors.HexColor('#32cdcd')
FOG = colors.HexColor('#f2f3f4')
NOTICE = "Seller-provided information. Not independently verified by Broker. Not a warranty or a substitute for the Seller's Disclosure Notice."
BODY = ParagraphStyle('body', fontName='Helvetica', fontSize=9, leading=13, textColor=INK, spaceAfter=5, splitLongWords=True)
SMALL = ParagraphStyle('small', parent=BODY, fontSize=8, leading=10)
HEADING = ParagraphStyle('heading', parent=BODY, fontName='Helvetica-BoldOblique', fontSize=15, leading=19, spaceBefore=13, spaceAfter=9)
LABEL = ParagraphStyle('label', parent=BODY, fontName='Helvetica-Bold', fontSize=9)


def p(value, style=BODY):
    return Paragraph(escape(display_value(value)).replace('\n','<br/>'), style)


def _page(canvas, doc, *, title, address, revision, first=False):
    canvas.saveState()
    if first:
        canvas.setFillColor(colors.black); canvas.rect(0, 641, 612, 151, fill=1, stroke=0)
        canvas.setFillColor(TEAL if 'Appraiser' in title else colors.HexColor('#f4bb32'))
        canvas.roundRect(43.2, 743, 208, 18, 9, fill=1, stroke=0)
        canvas.setFillColor(colors.black); canvas.setFont('Courier-Bold',8)
        canvas.drawString(54,749,title.upper())
        canvas.setFillColor(colors.white); canvas.setFont('Helvetica-BoldOblique',24)
        address_lines = p(address, ParagraphStyle('address',fontName='Helvetica-BoldOblique',fontSize=23,leading=27,textColor=colors.white))
        _,h=address_lines.wrap(430,60); address_lines.drawOn(canvas,43.2,725-h)
        canvas.setFont('Helvetica',9); canvas.drawString(43.2,659,f'Seller-reported home information | Response revision {revision}')
        mark=Path(__file__).resolve().parents[2]/'static/images/brand/origen-og-mark.png'
        if mark.exists(): canvas.drawImage(str(mark),510,693,width=59,height=50,mask='auto',preserveAspectRatio=True)
        canvas.setStrokeColor(TEAL); canvas.setLineWidth(4); canvas.line(0,641,612,641)
        canvas.setStrokeColor(colors.HexColor('#f4bb32')); canvas.setLineWidth(3); canvas.line(0,633,612,633)
    else:
        canvas.setFont('Helvetica-Bold',9); canvas.setFillColor(INK); canvas.drawString(43.2,755,title.upper())
        canvas.setStrokeColor(TEAL); canvas.setLineWidth(1); canvas.line(43.2,747,568.8,747)
    canvas.setStrokeColor(colors.HexColor('#b6bcc1')); canvas.setLineWidth(.4); canvas.line(43.2,41,568.8,41)
    canvas.setFont('Helvetica',6); canvas.setFillColor(colors.HexColor('#555555')); canvas.drawString(43.2,31,NOTICE)
    canvas.setFont('Courier',6); canvas.drawRightString(568.8,20,f'ORIGEN REALTY | PAGE {doc.page}')
    canvas.restoreState()


def _table(rows, widths=None):
    table=Table(rows,colWidths=widths or [WIDTH*.36,WIDTH*.64],repeatRows=0,hAlign='LEFT',splitInRow=1)
    table.setStyle(TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,0),(-1,-1),[FOG,colors.white]),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d2d6d9'))]))
    return table


def report_pdf(answers, kind, *, address, revision):
    if kind not in ('appraiser','buyer'):
        raise ValueError('Unknown report type')
    title='Appraiser Property Brief' if kind=='appraiser' else 'Buyer Home Guide'
    buffer=BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=(612,792),leftMargin=43.2,rightMargin=43.2,
        topMargin=58,bottomMargin=55,title=title,author='Origen Realty')
    story=[Spacer(1,124)]
    intro=("Prepared for the appraiser at the property inspection. This brief organizes seller-reported improvements, system ages and site features. No opinion of value is requested or implied. Improvement costs describe spending and are not contributory value."
        if kind=='appraiser' else "Home information supplied by the seller, including systems, operating costs, warranties and what stays. Dates and costs are estimates where described. Obtain your own quotes and confirm final terms in the sales contract.")
    story += [p(intro),Spacer(1,10)]
    answers=answers or {}
    any_content=False
    for section in manifest()['sections']:
        groups=[]
        for group in section['groups']:
            rows=[]
            for field in group['fields']:
                value=answers.get(field['id'])
                if value is None or value=='' or value==[] or kind not in field['audience']:
                    continue
                rows.append([p(field['label'],SMALL),p(value,SMALL)])
            if rows: groups.append((group,rows))
        if not groups:
            continue
        any_content=True
        story.append(p(section['title'],HEADING))
        if section['id']=='improvements':
            story.append(p('Seller-reported spending is provided only in the appraiser brief. Scope and dates are based on seller knowledge.' if kind=='appraiser' else 'Improvement details are supplied by the seller. Confirm any warranty transfer requirements.',SMALL))
        for group,rows in groups:
            story.append(KeepTogether([p(group['title'],LABEL),_table(rows[:1])]))
            if len(rows)>1: story.append(_table(rows[1:]))
            story.append(Spacer(1,6))
    if not any_content:
        story.append(p('The seller submitted the questionnaire without additional property facts.'))
    story += [Spacer(1,12),p('Only supplied answers appear here. Unanswered questions are omitted. Documents are included only when your agent selects them for sharing.',SMALL)]
    doc.build(story,onFirstPage=lambda c,d:_page(c,d,title=title,address=address,revision=revision,first=True),
              onLaterPages=lambda c,d:_page(c,d,title=title,address=address,revision=revision))
    return buffer.getvalue()


def questionnaire_pdf(answers, version=1):
    definition=manifest(version)
    writer=PdfWriter(clone_from=ROOT/f'v{version}'/'questionnaire.pdf')
    values={}
    complete_values={}
    overflow=[]
    for key,field in fields(version).items():
        value=answers.get(key)
        for widget in field['widgets']:
            if field['kind']!='text':
                selected=widget['option'] in (value if isinstance(value,list) else [value])
                values[widget['name']]='/Yes' if selected else '/Off'
                continue
            text=display_value(value)
            complete_values[widget['name']]=text
            x1,y1,x2,y2=widget['rect']
            width=max(10,x2-x1-5)
            # Keep a readable fixed font. Full answers also print in continuations.
            max_lines=max(1,int((y2-y1-3)/10)) if field['multiline'] else 1
            lines=[]
            for paragraph in text.split('\n'):
                line=''
                for character in paragraph:
                    if stringWidth(line+character,'Helvetica',8)>width:
                        lines.append(line); line=''
                    line+=character
                lines.append(line)
            if len(lines)>max_lines:
                marker=f'[See note {len(overflow)+1}]'
                overflow.append((field['label'],widget['page'],text))
                values[widget['name']]=marker
            else:
                values[widget['name']]=text
    for page in writer.pages:
        for ref in page.get('/Annots',[]):
            widget=ref.get_object()
            if widget.get('/FT')=='/Tx':
                widget[NameObject('/DA')]=TextStringObject('/Helv 8 Tf 0 g')
    writer.update_page_form_field_values(None,values,auto_regenerate=False)
    # Keep full canonical values for export/editing. Overflow appearances point
    # explicitly to a printed continuation, never an unreadable tiny font.
    for page in writer.pages:
        for ref in page.get('/Annots',[]):
            widget=ref.get_object()
            if str(widget.get('/T')) in complete_values:
                widget[NameObject('/V')]=TextStringObject(complete_values[str(widget['/T'])])
    if overflow:
        continuation=BytesIO()
        doc=SimpleDocTemplate(continuation,pagesize=(612,792),leftMargin=43.2,rightMargin=43.2,topMargin=58,bottomMargin=55)
        story=[p('Home Facts continuation',HEADING)]
        for i,(label,page,text) in enumerate(overflow,1):
            story += [p(f'Note {i}, page {page}: {label}',LABEL),p(text),Spacer(1,8)]
        doc.build(story,onFirstPage=lambda c,d:_page(c,d,title='Home Facts continuation',address='',revision=''),onLaterPages=lambda c,d:_page(c,d,title='Home Facts continuation',address='',revision=''))
        for page in PdfReader(continuation).pages:
            writer.add_page(page)
    output=BytesIO(); writer.write(output)
    return output.getvalue()
