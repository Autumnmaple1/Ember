"""Run with the Codex bundled Python. Render DOCX separately for visual QA."""
from pathlib import Path
import re
import shutil
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'docs' / 'midterm' / 'assets'
OUT = ROOT / 'output' / 'midterm'
FONT = 'C:/Windows/Fonts/msyh.ttc'
BOLD = 'C:/Windows/Fonts/msyhbd.ttc'


def diagram(name, boxes, arrows, size):
    image = Image.new('RGB', size, '#ffffff')
    draw = ImageDraw.Draw(image)
    if name == 'architecture.png':
        draw.line((248,460,1297,460), fill='#566b60', width=4)
    for rectangle, title, body in boxes:
        draw.rounded_rectangle(rectangle, radius=13, fill='#f2f5f2', outline='#8b9d93', width=2)
        x0, y0, x1, y1 = rectangle
        draw.text(((x0+x1)/2, y0+24), title, font=ImageFont.truetype(BOLD, 27), fill='#1f332a', anchor='mt')
        for j, line in enumerate(body):
            draw.text(((x0+x1)/2, y0+73+j*35), line, font=ImageFont.truetype(FONT, 23), fill='#33463d', anchor='mt')
    for x0, y0, x1, y1 in arrows:
        draw.line((x0, y0, x1, y1), fill='#566b60', width=4)
        if x0 == x1:
            draw.polygon([(x1,y1),(x1-9,y1-15),(x1+9,y1-15)], fill='#566b60')
        else:
            draw.polygon([(x1,y1),(x1-15,y1-9),(x1-15,y1+9)], fill='#566b60')
    image.save(ASSETS / name)


def prepare_images():
    ASSETS.mkdir(parents=True, exist_ok=True)
    for src, target in [('01-stage.png', 'stage.png'), ('02-project.png', 'project.png')]:
        shutil.copy2(OUT / 'qa' / src, ASSETS / target)
    diagram('loop.png', [((10+index*300,25,270+index*300,190), title, body)
        for index,(title,body) in enumerate([('确认此刻',['时间与双方场景']),('表达意愿',['发出与核对邀请']),
            ('共同做事',['选择与讨论']),('留下成果',['记录与来源']),('下次继续',['恢复未完问题'])])],
        [(273+i*300,107,305+i*300,107) for i in range(4)], (1500,220))
    diagram('architecture.png', [
        ((15,20,400,175),'React 前端',['对话  舞台  活动  回忆']),
        ((550,20,990,175),'FastAPI 与事件协议',['消息标识  世界版本  状态同步']),
        ((1140,20,1530,175),'模型服务',['状态  回复  编码  语音']),
        ((550,265,990,430),'事件总线与业务规则',['时钟  场景  活动  串行任务']),
        ((20,495,475,665),'PostgreSQL pgvector',['历史消息  长期记忆向量']),
        ((550,495,990,665),'本地状态与可恢复待办',['玩家成果  中期摘要  编码待办']),
        ((1065,495,1530,665),'生产存档与可选图谱',['备份恢复  Neo4j 检索'])],
        [(405,97,545,97),(995,97,1135,97),(770,180,770,260),
         (770,435,770,490),(248,460,248,490),(1297,460,1297,490)], (1550,690))


def set_font(style, size, bold=False):
    style.font.name = 'Microsoft YaHei'
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.italic = False
    style.font.color.rgb = RGBColor(0,0,0)
    style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')


def add_table(document, rows):
    rows = [re.split(r'\s*\|\s*', line.strip().strip('|')) for line in rows]
    rows = [r for r in rows if not all(re.fullmatch('[-: ]+', x) for x in r)]
    table = document.add_table(rows=0, cols=len(rows[0]))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    widths = [1.25, 2.65, 3.1] if len(rows[0]) == 3 else [7/len(rows[0])] * len(rows[0])
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    borders = OxmlElement('w:tblBorders')
    for edge in ('top','left','bottom','right','insideH','insideV'):
        item = OxmlElement('w:'+edge)
        for key,value in [('val','single'),('sz','6'),('color','D9D9D9')]:
            item.set(qn('w:'+key), value)
        borders.append(item)
    table._tbl.tblPr.append(borders)
    for i,row in enumerate(rows):
        cells = table.add_row().cells
        for j,text in enumerate(row):
            cell = cells[j]
            cell.width = Inches(widths[j])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tcpr = cell._tc.get_or_add_tcPr()
            margin = OxmlElement('w:tcMar')
            for edge in ('top','left','bottom','right'):
                item=OxmlElement('w:'+edge); item.set(qn('w:w'),'95'); item.set(qn('w:type'),'dxa'); margin.append(item)
            tcpr.append(margin)
            if i == 0 or i % 2 == 0:
                shade=OxmlElement('w:shd'); shade.set(qn('w:fill'),'343A40' if i==0 else 'F3F5F3'); tcpr.append(shade)
            p=cell.paragraphs[0]; p.paragraph_format.space_after=Pt(2); p.paragraph_format.space_before=Pt(2)
            p.paragraph_format.line_spacing=1.13
            r=p.add_run(text); r.font.size=Pt(10)
            if i==0: r.bold=True; r.font.color.rgb=RGBColor(255,255,255)
            if j==0: p.alignment=1
        rowpr=table.rows[-1]._tr.get_or_add_trPr()
        no_split=OxmlElement('w:cantSplit'); rowpr.append(no_split)
        if i==0: rowpr.append(OxmlElement('w:tblHeader'))
    document.add_paragraph().paragraph_format.space_after=Pt(0)


def main():
    prepare_images()
    document = Document()
    section=document.sections[0]
    section.page_width,section.page_height=Inches(8.5),Inches(11)
    section.top_margin,section.bottom_margin=Inches(.67),Inches(.67)
    section.left_margin,section.right_margin=Inches(.75),Inches(.75)
    section.header_distance=section.footer_distance=Inches(.3)
    set_font(document.styles['Normal'],11)
    document.styles['Normal'].paragraph_format.line_spacing=1.18
    document.styles['Normal'].paragraph_format.space_after=Pt(7)
    for name,size in [('Title',25),('Subtitle',13),('Heading 1',16),('Heading 2',12)]:
        set_font(document.styles[name],size,name.startswith('Heading'))
    for style in document.styles:
        for border in list(style.element.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    document.styles['Heading 1'].paragraph_format.space_after=Pt(12)
    header=section.header.paragraphs[0]; header.text='Ember 项目中期策划案'; header.style='Normal'
    header.runs[0].font.size=Pt(8)
    footer=section.footer.paragraphs[0]; footer.alignment=2
    footer.add_run('中期评审材料  2026-11-02  |  ').font.size=Pt(8)
    field=OxmlElement('w:fldSimple'); field.set(qn('w:instr'),'PAGE'); footer._p.append(field)
    lines=(ROOT/'docs/midterm/proposal.md').read_text(encoding='utf-8').splitlines()
    table=[]; first=True; intro_count=0
    for line in lines+['']:
        if line.startswith('|'):
            table.append(line); continue
        if table: add_table(document,table); table=[]
        if not line.strip(): continue
        if line.startswith('# '):
            document.add_paragraph(line[2:],'Title'); continue
        if line.startswith('## '):
            p=document.add_paragraph(line[3:],'Heading 1')
            if not first: p.paragraph_format.page_break_before=True
            first=False
            continue
        match=re.fullmatch(r'!\[(.*?)\]\((.*?)\)',line)
        if match:
            p=document.add_paragraph(); p.paragraph_format.keep_with_next=True
            width = 6 if match.group(2).endswith(('stage.png','project.png')) else 7
            p.alignment=1
            p.add_run().add_picture(str(ROOT/'docs/midterm'/match.group(2)),width=Inches(width))
            caption=document.add_paragraph(match.group(1)); caption.paragraph_format.space_after=Pt(9)
            caption.runs[0].font.size=Pt(9); continue
        if first:
            document.add_paragraph(line,'Subtitle' if intro_count==0 else 'Normal'); intro_count+=1
        else: document.add_paragraph(line)
    document.core_properties.title='Ember 中期项目策划案'
    document.core_properties.subject='持续角色陪伴与共同活动'
    document.core_properties.author='Ember 项目'
    OUT.mkdir(parents=True,exist_ok=True)
    target=OUT/'Ember中期策划案.docx'; document.save(target)
    print(target)


if __name__=='__main__': main()
