// main.js
// Set worker path for PDF.js
pdfjsLib.GlobalWorkerOptions.workerSrc = 'lib/pdf.worker.min.js';

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const pdfViewer = document.getElementById('pdf-viewer');
const pdfPages = document.getElementById('pdf-pages');

// Drag & Drop handlers
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  
  const file = e.dataTransfer.files[0];
  if (file && file.type === 'application/pdf') {
    loadPDF(file);
  }
});

// File input fallback
fileInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) {
    loadPDF(file);
  }
});

// Click to trigger file input
dropZone.addEventListener('click', () => {
  fileInput.click();
});

// Load and render PDF
async function loadPDF(file) {
  const arrayBuffer = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
  
  // Hide drop zone, show viewer
  dropZone.classList.add('hidden');
  pdfViewer.classList.remove('hidden');
  
  // Clear previous pages
  pdfPages.innerHTML = '';
  
  // Render each page
  for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
    const page = await pdf.getPage(pageNum);
    const canvas = await renderPage(page, pageNum);
    pdfPages.appendChild(canvas);
  }
  
  console.log(`Loaded PDF with ${pdf.numPages} pages`);
}

// Render a single page to canvas
async function renderPage(page, pageNum) {
  const scale = 1.5;
  const viewport = page.getViewport({ scale });
  
  const canvas = document.createElement('canvas');
  canvas.dataset.pageNum = pageNum;
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  
  const context = canvas.getContext('2d');
  await page.render({
    canvasContext: context,
    viewport: viewport
  }).promise;
  
  return canvas;
}
