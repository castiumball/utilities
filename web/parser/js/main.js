**
 * PDF Parser - Main Application
 * 
 * Handles PDF upload, rendering, and parsing via backend API.
 * 
 * Architecture:
 * - PDF rendering: Uses PDF.js to render pages client-side
 * - Parsing: Sends PDF to Flask backend which runs Python processors
 * - Output: Displays JSON result with syntax highlighting
 */

// ============================================
// Configuration
// ============================================

const CONFIG = {
  // API endpoint for backend server
  API_BASE_URL: 'http://localhost:5000',
  
  // PDF.js rendering scale (1.5 = 150% of original size for readability)
  PDF_RENDER_SCALE: 1.5,
  
  // Intersection Observer threshold for page tracking
  // 0.5 = trigger when 50% of page is visible in viewport
  PAGE_VISIBILITY_THRESHOLD: 0.5,
  
  // Duration to show "copied" feedback on copy button (milliseconds)
  COPY_FEEDBACK_DURATION_MS: 1500,
  
  // Small delay between rendering pages to allow UI thread to update
  RENDER_DELAY_MS: 10,
};

// Configure PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = 'lib/pdf.worker.mjs';


// ============================================
// DOM Element References
// ============================================
// Grouped by feature area for easier navigation

// PDF Upload
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

// PDF Loading Progress
const loadingIndicator = document.getElementById('loading-indicator');
const progressBar = document.getElementById('progress-bar');
const progressText = document.getElementById('progress-text');

// PDF Viewer
const pdfViewer = document.getElementById('pdf-viewer');
const pdfPages = document.getElementById('pdf-pages');
const pageIndicator = document.getElementById('page-indicator');
const currentPageNumDisplay = document.getElementById('current-page-num');

// Parser Controls
const processorSelect = document.getElementById('processor-select');
const parseButton = document.getElementById('parse-btn');
const copyButton = document.getElementById('copy-btn');

// Output Display
const outputPlaceholder = document.getElementById('output-placeholder');
const outputLoading = document.getElementById('output-loading');
const outputError = document.getElementById('output-error');
const outputErrorMessage = document.getElementById('error-message');
const outputJson = document.getElementById('output-json');
const jsonContentElement = document.getElementById('json-content');


// ============================================
// Application State
// ============================================

const state = {
  currentFile: null,        // Currently loaded PDF File object
  currentPageNumber: null,  // Page number currently visible in viewport
};


// ============================================
// Initialization
// ============================================

document.addEventListener('DOMContentLoaded', initializeApp);

/**
 * Bootstrap the application on page load.
 */
function initializeApp() {
  fetchAvailableProcessors();
  attachEventListeners();
}


// ============================================
// API Communication
// ============================================

/**
 * Fetch list of available processors from the backend.
 * Populates the processor dropdown on success.
 */
async function fetchAvailableProcessors() {
  try {
    const response = await fetch(`${CONFIG.API_BASE_URL}/processors`);
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    
    const data = await response.json();
    populateProcessorDropdown(data.processors);
    
  } catch (error) {
    console.error('Failed to fetch processors:', error);
    showProcessorDropdownError('Server offline');
  }
}

/**
 * Send PDF to backend for parsing with selected processor.
 * 
 * @param {File} file - The PDF file to parse
 * @param {string} processorName - Name of the processor to use
 * @returns {Promise<Object>} Parsed result from backend
 */
async function requestPdfParse(file, processorName) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('processor', processorName);
  
  const response = await fetch(`${CONFIG.API_BASE_URL}/parse`, {
    method: 'POST',
    body: formData,
  });
  
  const data = await response.json();
  
  if (!response.ok) {
    throw new Error(data.error || 'Parse failed');
  }
  
  return data.result;
}


// ============================================
// Event Handling
// ============================================

/**
 * Attach all event listeners.
 * Centralized here for easy overview of user interactions.
 */
function attachEventListeners() {
  // Drag & drop events
  dropZone.addEventListener('dragover', handleDragOver);
  dropZone.addEventListener('dragleave', handleDragLeave);
  dropZone.addEventListener('drop', handleFileDrop);
  
  // Click on drop zone opens file picker
  dropZone.addEventListener('click', () => fileInput.click());
  
  // Keyboard accessibility for drop zone
  dropZone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.click();
    }
  });
  
  // File input change (from file picker dialog)
  fileInput.addEventListener('change', handleFileInputChange);
  
  // Parser controls
  parseButton.addEventListener('click', handleParseClick);
  copyButton.addEventListener('click', handleCopyClick);
  processorSelect.addEventListener('change', updateParseButtonState);
}

/**
 * Handle dragover event - show visual feedback.
 */
function handleDragOver(event) {
  event.preventDefault();
  dropZone.classList.add('drag-over');
}

/**
 * Handle dragleave event - remove visual feedback.
 */
function handleDragLeave() {
  dropZone.classList.remove('drag-over');
}

/**
 * Handle file drop event.
 */
function handleFileDrop(event) {
  event.preventDefault();
  dropZone.classList.remove('drag-over');
  
  const file = event.dataTransfer.files[0];
  if (isValidPdfFile(file)) {
    loadPdfFile(file);
  }
}

/**
 * Handle file selection via input element.
 */
function handleFileInputChange(event) {
  const file = event.target.files[0];
  if (file) {
    loadPdfFile(file);
  }
}

/**
 * Handle parse button click.
 */
async function handleParseClick() {
  if (!state.currentFile || !processorSelect.value) {
    return;
  }
  
  showOutputState('loading');
  parseButton.disabled = true;
  
  try {
    const result = await requestPdfParse(state.currentFile, processorSelect.value);
    displayParseResult(result);
  } catch (error) {
    console.error('Parse error:', error);
    showOutputError(error.message || 'Failed to connect to server');
  } finally {
    updateParseButtonState();
  }
}

/**
 * Handle copy button click - copy JSON to clipboard.
 */
async function handleCopyClick() {
  const jsonText = jsonContentElement.textContent;
  
  try {
    await navigator.clipboard.writeText(jsonText);
    showCopyFeedback();
  } catch (error) {
    console.error('Failed to copy to clipboard:', error);
  }
}


// ============================================
// PDF Loading & Rendering
// ============================================

/**
 * Load and render a PDF file.
 * 
 * @param {File} file - PDF file to load
 */
async function loadPdfFile(file) {
  state.currentFile = file;
  updateParseButtonState();
  
  // Transition UI to loading state
  hideElement(dropZone);
  showElement(loadingIndicator);
  resetProgress();
  showOutputState('placeholder');
  
  try {
    const arrayBuffer = await file.arrayBuffer();
    const pdfDocument = await pdfjsLib.getDocument({
      data: arrayBuffer,
      // Disable font loading for faster rendering and smaller memory footprint
      disableFontFace: true,
    }).promise;
    
    await renderAllPages(pdfDocument);
    
    // Transition UI to viewer state
    hideElement(loadingIndicator);
    showElement(pdfViewer);
    showElement(pageIndicator);
    
    initializePageObserver();
    
    console.log(`Loaded PDF: ${file.name} (${pdfDocument.numPages} pages)`);
    
  } catch (error) {
    console.error('Failed to load PDF:', error);
    // Return to drop zone on error
    hideElement(loadingIndicator);
    showElement(dropZone);
  }
}

/**
 * Render all pages of a PDF document.
 * 
 * @param {PDFDocumentProxy} pdfDocument - PDF.js document object
 */
async function renderAllPages(pdfDocument) {
  pdfPages.innerHTML = '';
  
  for (let pageNum = 1; pageNum <= pdfDocument.numPages; pageNum++) {
    const page = await pdfDocument.getPage(pageNum);
    const canvas = await renderPageToCanvas(page, pageNum);
    pdfPages.appendChild(canvas);
    
    updateProgress(pageNum, pdfDocument.numPages);
    
    // Brief pause allows browser to update UI during long renders
    await sleep(CONFIG.RENDER_DELAY_MS);
  }
}

/**
 * Render a single PDF page to a canvas element.
 * 
 * @param {PDFPageProxy} page - PDF.js page object
 * @param {number} pageNumber - Page number (for data attribute)
 * @returns {HTMLCanvasElement} Rendered canvas
 */
async function renderPageToCanvas(page, pageNumber) {
  const viewport = page.getViewport({ scale: CONFIG.PDF_RENDER_SCALE });
  
  const canvas = document.createElement('canvas');
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  canvas.dataset.pageNum = pageNumber;
  canvas.classList.add('pdf-page');
  
  const context = canvas.getContext('2d');
  await page.render({
    canvasContext: context,
    viewport: viewport,
  }).promise;
  
  return canvas;
}

/**
 * Set up Intersection Observer to track which page is currently visible.
 * Updates the page indicator badge as user scrolls.
 */
function initializePageObserver() {
  const observerOptions = {
    root: null, // Use viewport as root
    rootMargin: '0px',
    threshold: CONFIG.PAGE_VISIBILITY_THRESHOLD,
  };

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        const pageNum = entry.target.dataset.pageNum;
        
        // Only update if page actually changed (avoid unnecessary updates)
        if (state.currentPageNumber !== pageNum) {
          state.currentPageNumber = pageNum;
          currentPageNumDisplay.textContent = pageNum;
        }
      }
    });
  }, observerOptions);

  // Observe all rendered page canvases
  document.querySelectorAll('.pdf-page').forEach((canvas) => {
    observer.observe(canvas);
  });
}


// ============================================
// UI State Management
// ============================================

/**
 * Populate processor dropdown with available options.
 * 
 * @param {string[]} processors - Array of processor names
 */
function populateProcessorDropdown(processors) {
  processorSelect.innerHTML = '';
  
  if (processors && processors.length > 0) {
    processors.forEach((processorName) => {
      const option = document.createElement('option');
      option.value = processorName;
      // Convert snake_case to readable label: "Reference_Card" -> "Reference Card"
      option.textContent = processorName.replace(/_/g, ' ');
      processorSelect.appendChild(option);
    });
    updateParseButtonState();
  } else {
    showProcessorDropdownError('No processors available');
  }
}

/**
 * Show error state in processor dropdown.
 */
function showProcessorDropdownError(message) {
  processorSelect.innerHTML = `<option value="">${message}</option>`;
}

/**
 * Update parse button enabled state based on current conditions.
 * Button requires both a file and a selected processor.
 */
function updateParseButtonState() {
  const hasFile = state.currentFile !== null;
  const hasProcessor = processorSelect.value !== '';
  parseButton.disabled = !(hasFile && hasProcessor);
}

/**
 * Show one of the output panel states.
 * 
 * @param {'placeholder' | 'loading' | 'error' | 'result'} stateName
 */
function showOutputState(stateName) {
  // Hide all states first
  hideElement(outputPlaceholder);
  hideElement(outputLoading);
  hideElement(outputError);
  hideElement(outputJson);
  hideElement(copyButton);
  
  // Show requested state
  switch (stateName) {
    case 'placeholder':
      showElement(outputPlaceholder);
      break;
    case 'loading':
      showElement(outputLoading);
      break;
    case 'error':
      showElement(outputError);
      break;
    case 'result':
      showElement(outputJson);
      showElement(copyButton);
      break;
  }
}

/**
 * Display an error message in the output panel.
 */
function showOutputError(message) {
  outputErrorMessage.textContent = message;
  showOutputState('error');
}

/**
 * Display parse result as syntax-highlighted JSON.
 */
function displayParseResult(result) {
  const formattedJson = JSON.stringify(result, null, 2);
  jsonContentElement.innerHTML = applySyntaxHighlighting(formattedJson);
  showOutputState('result');
}

/**
 * Show visual feedback when JSON is copied.
 */
function showCopyFeedback() {
  const originalContent = copyButton.innerHTML;
  copyButton.textContent = '✓';
  copyButton.classList.add('copied');
  
  setTimeout(() => {
    copyButton.innerHTML = originalContent;
    copyButton.classList.remove('copied');
  }, CONFIG.COPY_FEEDBACK_DURATION_MS);
}

/**
 * Update progress bar and text.
 */
function updateProgress(current, total) {
  const percent = Math.round((current / total) * 100);
  progressBar.style.width = `${percent}%`;
  progressText.textContent = `${percent}%`;
}

/**
 * Reset progress bar to initial state.
 */
function resetProgress() {
  progressBar.style.width = '0%';
  progressText.textContent = '0%';
}


// ============================================
// Utility Functions
// ============================================

/**
 * Check if a file is a valid PDF.
 */
function isValidPdfFile(file) {
  return file && file.type === 'application/pdf';
}

/**
 * Show an element by removing the hidden class.
 */
function showElement(element) {
  element.classList.remove('hidden');
}

/**
 * Hide an element by adding the hidden class.
 */
function hideElement(element) {
  element.classList.add('hidden');
}

/**
 * Promise-based sleep/delay utility.
 * 
 * @param {number} ms - Milliseconds to wait
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Apply syntax highlighting to JSON string for display.
 * Returns HTML with span elements for each token type.
 * 
 * @param {string} json - JSON string to highlight
 * @returns {string} HTML with syntax highlighting spans
 */
function applySyntaxHighlighting(json) {
  // Escape HTML special characters first to prevent XSS
  const escaped = json
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  
  // Regex matches JSON tokens: strings (with optional colon for keys), 
  // booleans, null, and numbers
  const tokenPattern = /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g;
  
  return escaped.replace(tokenPattern, (match) => {
    let tokenClass = 'json-number';
    
    if (match.startsWith('"')) {
      // String or key - keys end with ":"
      tokenClass = match.endsWith(':') ? 'json-key' : 'json-string';
    } else if (/^(true|false)$/.test(match)) {
      tokenClass = 'json-boolean';
    } else if (match === 'null') {
      tokenClass = 'json-null';
    }
    
    return `<span class="${tokenClass}">${match}</span>`;
  });
}
