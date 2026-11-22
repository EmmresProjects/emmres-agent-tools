/**
 * Presentation Layout Forge helpers for Google Slides.
 * Paste into Extensions > Apps Script in a Google Slides deck.
 * Run listLayoutForgeCapabilities() first. No network calls are used.
 */
const LAYOUT_FORGE = Object.freeze({
  title: {left: 28.8, top: 27.4, width: 662.4, height: 41.8, font: 'Arial', size: 24, color: '#000000'},
  shape: {fill: '#FFFFFF', border: '#000000', borderWeight: 1},
  table: {headerFill: '#000000', headerText: '#FFFFFF', altFill: '#D9D9D9'},
  line: {color: '#000000', weight: 1, dash: 'SOLID', start: 'NONE', end: 'FILL_ARROW'}
});

function listLayoutForgeCapabilities() {
  const result = {
    capabilities: ['formatTitles','formatShapes','formatLines','formatTables'],
    settings: LAYOUT_FORGE
  };
  console.log(JSON.stringify(result, null, 2));
  return result;
}

function runLayoutForge() {
  const presentation = SlidesApp.getActivePresentation();
  presentation.getSlides().forEach((slide) => {
    formatSlideTitles_(slide);
    formatSlideShapes_(slide);
    formatSlideLines_(slide);
    formatSlideTables_(slide);
  });
  return {ok: true, slides: presentation.getSlides().length};
}

function formatSlideTitles_(slide) {
  const title = slide.getPlaceholder(SlidesApp.PlaceholderType.TITLE) || slide.getPlaceholder(SlidesApp.PlaceholderType.CENTERED_TITLE);
  if (!title) return;
  title.setLeft(LAYOUT_FORGE.title.left).setTop(LAYOUT_FORGE.title.top).setWidth(LAYOUT_FORGE.title.width).setHeight(LAYOUT_FORGE.title.height);
  title.getText().getTextStyle().setFontFamily(LAYOUT_FORGE.title.font).setFontSize(LAYOUT_FORGE.title.size).setForegroundColor(LAYOUT_FORGE.title.color);
}

function formatSlideShapes_(slide) {
  slide.getShapes().forEach((shape) => {
    if (shape.getShapeType() === SlidesApp.ShapeType.TEXT_BOX) return;
    shape.getFill().setSolidFill(LAYOUT_FORGE.shape.fill);
    shape.getBorder().getLineFill().setSolidFill(LAYOUT_FORGE.shape.border);
    shape.getBorder().setWeight(LAYOUT_FORGE.shape.borderWeight);
  });
}

function formatSlideLines_(slide) {
  slide.getLines().forEach((line) => {
    line.getLineFill().setSolidFill(LAYOUT_FORGE.line.color);
    line.setWeight(LAYOUT_FORGE.line.weight);
    line.setDashStyle(SlidesApp.DashStyle[LAYOUT_FORGE.line.dash]);
    line.setStartArrow(SlidesApp.ArrowStyle[LAYOUT_FORGE.line.start]);
    line.setEndArrow(SlidesApp.ArrowStyle[LAYOUT_FORGE.line.end]);
  });
}

function formatSlideTables_(slide) {
  slide.getTables().forEach((table) => {
    for (let row = 0; row < table.getNumRows(); row++) {
      for (let col = 0; col < table.getNumColumns(); col++) {
        const cell = table.getCell(row, col);
        cell.getFill().setSolidFill(row === 0 ? LAYOUT_FORGE.table.headerFill : (row % 2 === 0 ? LAYOUT_FORGE.table.altFill : '#FFFFFF'));
        cell.getText().getTextStyle().setForegroundColor(row === 0 ? LAYOUT_FORGE.table.headerText : '#000000').setBold(row === 0);
      }
    }
  });
}

