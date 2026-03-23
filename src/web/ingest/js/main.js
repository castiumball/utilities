/**
 * Ingestion - Main Application
 *
 * Handles PDF upload, rendering, parsing, and graph pipeline via backend API.
 *
 * Architecture:
 * - PDF rendering: Uses PDF.js to render pages client-side
 * - Parsing: Sends PDF to Flask backend which runs Python processors
 * - Pipeline: Entity extraction and graph ingestion via graph API
 * - Output: Displays JSON result with syntax highlighting
 */



// ============================================
// Configuration
// ============================================

const CONFIG = {
    // API endpoint for backend server.
    // Auto-detected from the current URL so it works behind the reverse
    // proxy (/polaris_v1/ingest/ → base is /polaris_v1) AND when running
    // Flask directly (http://localhost:7999/ingest/ → base is empty).
    API_BASE_URL: window.location.pathname.startsWith('/polaris_v1')
      ? '/polaris_v1'
      : '',
    
    // PDF.js rendering scale (1.5 = 150% of original size for readability)
    PDF_RENDER_SCALE: 1.5,
    
    // Intersection Observer threshold for page tracking
    // 0.5 = trigger when 50% of page is visible in viewport
    PAGE_VISIBILITY_THRESHOLD: 0.5,
    
    // Duration to show "copied" feedback on copy button (milliseconds)
    COPY_FEEDBACK_DURATION_MS: 1500,
    
    // Small delay between rendering pages to allow UI thread to update
    RENDER_DELAY_MS: 10,

    // Debounce auto-parse when page changes
    PARSE_DEBOUNCE_MS: 250,
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
  const dropZoneMeta = document.getElementById('drop-zone-meta');
  const dropZoneFileName = document.getElementById('drop-zone-file-name');
  const dropZoneFileSize = document.getElementById('drop-zone-file-size');
  
  // PDF Loading Progress
  const loadingIndicator = document.getElementById('loading-indicator');
  const progressBar = document.getElementById('progress-bar');
  const progressText = document.getElementById('progress-text');
  
  // PDF Viewer
  const pdfViewer = document.getElementById('pdf-viewer');
  const pdfPages = document.getElementById('pdf-pages');
  const pageIndicator = document.getElementById('page-indicator');
  const currentPageNumDisplay = document.getElementById('current-page-num');
  const markdownPanel = document.getElementById('markdown-panel');
  const markdownToggle = document.getElementById('markdown-toggle');
  const markdownContent = document.getElementById('markdown-content');
  
  // Parser Controls
  const parserSelect = document.getElementById('parser-select');
  const saveChunksButton = document.getElementById('save-chunks-btn');
  const copyButton = document.getElementById('copy-btn');
  const flagButton = document.getElementById('flag-btn');
  const flagWrapper = document.getElementById('flag-wrapper');
  const flagNoteDisplay = document.getElementById('flag-note');
  const flagNav = document.getElementById('flag-nav');
  const flagPrevBtn = document.getElementById('flag-prev-btn');
  const flagNextBtn = document.getElementById('flag-next-btn');
  const flagSelect = document.getElementById('flag-select');
  const pauseParseButton = document.getElementById('pause-parse-btn');

  // Output Display
  const outputPlaceholder = document.getElementById('output-placeholder');
  const outputLoading = document.getElementById('output-loading');
  const outputError = document.getElementById('output-error');
  const outputErrorMessage = document.getElementById('error-message');
  const outputJson = document.getElementById('output-json');
  const jsonContentElement = document.getElementById('json-content');
  
  // Document Sidebar
  const documentSidebar = document.getElementById('document-sidebar');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const documentSearch = document.getElementById('document-search');
  const uploadDocumentButton = document.getElementById('upload-document-button');
  const pinnedDocumentsList = document.getElementById('pinned-documents');
  const allDocumentsList = document.getElementById('all-documents');

  // Output Tabs
  const tabParse = document.getElementById('tab-parse');
  const tabPipeline = document.getElementById('tab-pipeline');
  const tabpanelParse = document.getElementById('tabpanel-parse');
  const tabpanelPipeline = document.getElementById('tabpanel-pipeline');

  // Pipeline Controls
  const runAllBtn = document.getElementById('run-all-btn');
  const pipelineOutput = document.getElementById('pipeline-output');
  const pipelineOutputSummary = document.getElementById('pipeline-output-summary');
  const pipelineOutputToggle = document.getElementById('pipeline-output-toggle');
  const pipelineOutputJson = document.getElementById('pipeline-output-json');
  const pipelineJsonContent = document.getElementById('pipeline-json-content');
  const pipelineErrorDiv = document.getElementById('pipeline-error');
  const pipelineErrorMessage = document.getElementById('pipeline-error-message');
  const pipelineActionsDiv = document.getElementById('pipeline-actions');
  const deleteGraphBtn = document.getElementById('delete-graph-btn');
  const progressControl = document.getElementById('progress-control');
  const progressInput = document.getElementById('progress-input');
  const progressSetBtn = document.getElementById('progress-set-btn');
  
  
  // ============================================
  // Application State
  // ============================================
  
  const state = {
    currentFile: null,        // Currently loaded PDF File object
    currentPageNumber: null,  // Page number currently visible in viewport
    isParsing: false,         // Whether a parse request is in progress
    pendingParse: false,      // Whether a new parse should run after current
    lastParsedPage: null,     // Last page number parsed
    lastParsedParser: null,   // Last parser used for parsing
    parseDebounceId: null,    // Debounce timer for auto-parse
    parseNonce: 0,            // Increment to invalidate in-flight parses
    markdownVisible: false,   // Whether markdown panel is visible
    markdownDebounceId: null, // Debounce timer for markdown
    markdownNonce: 0,         // Increment to invalidate in-flight markdown
    currentDocHash: null,     // Current document hash from backend
    documents: [],            // Cached document list
    activeDocHash: null,      // Currently selected document hash
    flaggedPages: new Map(),  // page_number -> note (or null)
    autoParsePaused: false,   // Whether auto-parse is paused (output frozen)
    activeTab: 'parse',       // 'parse' or 'pipeline'
    pipelineStatus: null,     // Latest graph status from API
    pipelineRunning: false,   // Whether a pipeline step is running
    pipelineLastResult: null, // Last step result JSON for display
  };
  
  
  // ============================================
  // Initialization
  // ============================================
  
  document.addEventListener('DOMContentLoaded', initializeApp);
  
  /**
   * Bootstrap the application on page load.
   */
  function initializeApp() {
    fetchAvailableParsers();
    attachEventListeners();
    setDropZoneIdle();
    updateMarkdownVisibility();
    refreshDocumentList();
  }
  
  
  // ============================================
  // API Communication
  // ============================================
  
  /**
   * Fetch list of available parsers from the backend.
   * Populates the parser dropdown on success.
   */
  async function fetchAvailableParsers() {
    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/parsers`);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const data = await response.json();
      populateParserDropdown(data.parsers);
      
    } catch (error) {
      console.error('Failed to fetch parsers:', error);
      showParserDropdownError('Server offline');
    }
  }
  
  /**
   * Send PDF to backend for parsing with selected parser.
   * 
   * @param {File} file - The PDF file to parse
   * @param {string} parserName - Name of the parser to use
   * @param {{start: number, end: number} | null} pageRange - Page range to parse
   * @returns {Promise<Object>} Parsed result from backend
   */
  async function requestPdfParse(file, parserName, pageRange = null) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('parser', parserName);

    if (pageRange && Number.isFinite(pageRange.start) && Number.isFinite(pageRange.end)) {
      formData.append('page_start', String(pageRange.start));
      formData.append('page_end', String(pageRange.end));
    }

    const response = await fetch(`${CONFIG.API_BASE_URL}/api/parse`, {
      method: 'POST',
      body: formData,
    });

    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      throw new Error(
        `Server returned non-JSON response (${response.status}). ` +
        'The backend may be unreachable — check that gpw132 is running.'
      );
    }

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || 'Parse failed');
    }

    state.currentDocHash = data.hash || state.currentDocHash;
    return data.result;
  }

  /**
   * Fetch markdown for current page.
   * 
   * @param {{start: number, end: number}} pageRange
   * @returns {Promise<string>}
   */
  async function requestMarkdown(pageRange) {
    const hasHash = Boolean(state.currentDocHash);
    let response;

    if (hasHash) {
      response = await fetch(`${CONFIG.API_BASE_URL}/api/markdown`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hash: state.currentDocHash,
          page_start: pageRange.start,
          page_end: pageRange.end,
        }),
      });
    } else {
      const formData = new FormData();
      formData.append('file', state.currentFile);
      formData.append('page_start', String(pageRange.start));
      formData.append('page_end', String(pageRange.end));
      response = await fetch(`${CONFIG.API_BASE_URL}/api/markdown`, {
        method: 'POST',
        body: formData,
      });
    }

    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      throw new Error(
        `Server returned non-JSON response (${response.status}). ` +
        'The backend may be unreachable — check that gpw132 is running.'
      );
    }

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || 'Markdown fetch failed');
    }

    state.currentDocHash = data.hash || state.currentDocHash;
    return data.markdown || '';
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
    if (saveChunksButton) {
      saveChunksButton.addEventListener('click', handleSaveChunks);
    }
    copyButton.addEventListener('click', handleCopyClick);
    parserSelect.addEventListener('change', handleParserChange);
    if (markdownToggle) {
      markdownToggle.addEventListener('click', handleMarkdownToggle);
    }
    if (sidebarToggle) {
      sidebarToggle.addEventListener('click', handleSidebarToggle);
    }
    if (documentSearch) {
      documentSearch.addEventListener('input', handleSearchInput);
    }
    if (uploadDocumentButton) {
      uploadDocumentButton.addEventListener('click', () => fileInput.click());
    }
    if (flagButton) {
      flagButton.addEventListener('click', handleFlagClick);
    }
    if (flagPrevBtn) {
      flagPrevBtn.addEventListener('click', handleFlagPrev);
    }
    if (flagNextBtn) {
      flagNextBtn.addEventListener('click', handleFlagNext);
    }
    if (flagSelect) {
      flagSelect.addEventListener('change', handleFlagSelectChange);
    }
    if (pauseParseButton) {
      pauseParseButton.addEventListener('click', handlePauseParseClick);
    }

    // Output tabs
    if (tabParse) {
      tabParse.addEventListener('click', () => switchTab('parse'));
    }
    if (tabPipeline) {
      tabPipeline.addEventListener('click', () => switchTab('pipeline'));
    }

    // Pipeline controls
    if (runAllBtn) {
      runAllBtn.addEventListener('click', handleRunAll);
    }
    if (pipelineOutputToggle) {
      pipelineOutputToggle.addEventListener('click', handlePipelineJsonToggle);
    }
    if (deleteGraphBtn) {
      deleteGraphBtn.addEventListener('click', handleDeleteGraph);
    }
    if (progressSetBtn) {
      progressSetBtn.addEventListener('click', handleSetProgress);
    }
    document.querySelectorAll('.pipeline-step__run').forEach((btn) => {
      btn.addEventListener('click', handlePipelineStepRun);
    });
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
   * Handle parser dropdown changes.
   */
  function handleParserChange() {
    state.lastParsedPage = null;
    state.lastParsedParser = null;
    scheduleAutoParse();
    scheduleMarkdownFetch();
  }

  /**
   * Handle markdown toggle button click.
   */
  function handleMarkdownToggle() {
    state.markdownVisible = !state.markdownVisible;
    updateMarkdownVisibility();
    if (state.markdownVisible) {
      scheduleMarkdownFetch();
    }
  }

  function handleSidebarToggle() {
    if (!documentSidebar) {
      return;
    }
    documentSidebar.classList.toggle('document-sidebar--collapsed');
    if (sidebarToggle) {
      const isCollapsed = documentSidebar.classList.contains('document-sidebar--collapsed');
      sidebarToggle.setAttribute('aria-expanded', String(!isCollapsed));
    }
  }

  function handleSearchInput() {
    renderDocumentLists();
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

  /**
   * Handle Save Chunks button click - save parsed chunks to disk via API.
   * Sends full document (no page range) to the save-chunks endpoint.
   */
  async function handleSaveChunks() {
    if (!state.currentDocHash || !parserSelect.value) {
      return;
    }

    const originalText = saveChunksButton.querySelector('span').textContent;
    saveChunksButton.querySelector('span').textContent = 'Saving...';
    saveChunksButton.disabled = true;

    try {
      const response = await fetch(
        `${CONFIG.API_BASE_URL}/api/documents/${state.currentDocHash}/save-chunks`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ parser: parserSelect.value }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Save failed');
      }

      // Brief success feedback
      saveChunksButton.querySelector('span').textContent = `Saved ${data.chunks_saved} chunks`;
      saveChunksButton.classList.add('save-active');

      setTimeout(() => {
        saveChunksButton.querySelector('span').textContent = originalText;
        saveChunksButton.classList.remove('save-active');
        saveChunksButton.disabled = false;
      }, CONFIG.COPY_FEEDBACK_DURATION_MS);

    } catch (error) {
      console.error('Save chunks failed:', error);
      saveChunksButton.querySelector('span').textContent = 'Save failed';

      setTimeout(() => {
        saveChunksButton.querySelector('span').textContent = originalText;
        saveChunksButton.disabled = false;
      }, CONFIG.COPY_FEEDBACK_DURATION_MS);
    }
  }

  async function handleFlagClick() {
    if (!state.currentDocHash || !state.currentPageNumber) {
      return;
    }
    const pageNum = state.currentPageNumber;
    const isFlagged = state.flaggedPages.has(pageNum);

    if (!isFlagged) {
      const note = prompt(`Note for page ${pageNum} (optional):`);
      if (note === null) return; // cancelled
      try {
        const url = `${CONFIG.API_BASE_URL}/api/documents/${state.currentDocHash}/pages/${pageNum}/flag`;
        const body = { note: note.trim() || null };
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!response.ok) throw new Error('Flag failed');
        state.flaggedPages.set(pageNum, body.note);
      } catch (error) {
        console.error('Flag failed:', error);
        return;
      }
    } else {
      try {
        const url = `${CONFIG.API_BASE_URL}/api/documents/${state.currentDocHash}/pages/${pageNum}/flag`;
        const response = await fetch(url, { method: 'DELETE' });
        if (!response.ok) throw new Error('Unflag failed');
        state.flaggedPages.delete(pageNum);
      } catch (error) {
        console.error('Unflag failed:', error);
        return;
      }
    }

    updateFlagButton();
    updateFlagNavigation();

    state.documents = state.documents.map((doc) =>
      doc.hash === state.currentDocHash
        ? { ...doc, flagged_pages: (doc.flagged_pages || 0) + (isFlagged ? -1 : 1) }
        : doc
    );
    renderDocumentLists();
  }


  // ============================================
  // Pause Auto Parse
  // ============================================

  /**
   * Toggle auto-parse paused state.
   * While paused the output panel is frozen; the markdown panel still updates.
   * On resume, a fresh parse is triggered for the current page.
   */
  function handlePauseParseClick() {
    state.autoParsePaused = !state.autoParsePaused;
    updatePauseParseButton();

    if (!state.autoParsePaused) {
      // Resuming: clear last-parsed state so the current page gets parsed
      state.lastParsedPage = null;
      state.lastParsedParser = null;
      scheduleAutoParse();
    }
  }

  /**
   * Sync the pause button text, aria state, and active class.
   */
  function updatePauseParseButton() {
    if (!pauseParseButton) return;
    pauseParseButton.textContent = state.autoParsePaused ? 'Resume' : 'Pause';
    pauseParseButton.setAttribute('aria-pressed', String(state.autoParsePaused));
    pauseParseButton.classList.toggle('pause-active', state.autoParsePaused);
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
    state.currentPageNumber = null;
    state.currentDocHash = null;
    state.flaggedPages = new Map();
    updateFlagNavigation();
    resetAutoParseState();
    resetMarkdownState();
    setDropZoneLoaded(file);
    
    // Transition UI to loading state
    hideElement(pdfViewer);
    hideElement(pageIndicator);
    pdfPages.innerHTML = '';
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
      updateMarkdownVisibility();
      
      initializePageObserver();
      
      console.log(`Loaded PDF: ${file.name} (${pdfDocument.numPages} pages)`);
      
    } catch (error) {
      console.error('Failed to load PDF:', error);
      // Return to drop zone on error
      hideElement(loadingIndicator);
      state.currentFile = null;
      state.currentDocHash = null;
      resetAutoParseState();
      resetMarkdownState();
      setDropZoneIdle();
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
      root: pdfViewer,
      rootMargin: '0px',
      threshold: CONFIG.PAGE_VISIBILITY_THRESHOLD,
    };
  
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const pageNum = Number(entry.target.dataset.pageNum);
          
          // Only update if page actually changed (avoid unnecessary updates)
          if (state.currentPageNumber !== pageNum) {
            state.currentPageNumber = pageNum;
            currentPageNumDisplay.textContent = String(pageNum);
            scheduleAutoParse();
            scheduleMarkdownFetch();
            updateFlagButton();
            updateFlagNavSelection();
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
   * Set drop zone to idle state (no file selected).
   */
  function setDropZoneIdle() {
    dropZone.classList.remove('drop-zone--loaded');
    hideElement(dropZoneMeta);
    dropZoneFileName.textContent = '—';
    dropZoneFileSize.textContent = '—';
    state.currentPageNumber = null;
    resetAutoParseState();
    resetMarkdownState();
  }
  
  /**
   * Set drop zone to loaded state with file details.
   */
  function setDropZoneLoaded(file) {
    dropZone.classList.add('drop-zone--loaded');
    showElement(dropZoneMeta);
    dropZoneFileName.textContent = file.name;
    dropZoneFileSize.textContent = formatFileSize(file.size);
  }

  async function refreshDocumentList() {
    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/documents`);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to fetch documents');
      }
      state.documents = data.documents || [];
      renderDocumentLists();
    } catch (error) {
      console.error('Failed to load documents:', error);
    }
  }

  function renderDocumentLists() {
    if (!pinnedDocumentsList || !allDocumentsList) {
      return;
    }

    const query = documentSearch ? documentSearch.value.trim() : '';
    const filtered = applyFuzzyFilter(state.documents, query);
    const pinned = filtered.filter((doc) => doc.pinned);
    const unpinned = filtered.filter((doc) => !doc.pinned);

    pinnedDocumentsList.innerHTML = pinned.map(renderDocumentItem).join('');
    allDocumentsList.innerHTML = unpinned.map(renderDocumentItem).join('');

    attachPinHandlers();
    attachDocumentLoadHandlers();
    attachDeleteHandlers();
  }

  function renderDocumentItem(doc) {
    const status = doc.status || 'uploaded';
    const pinLabel = doc.pinned ? 'Unpin document' : 'Pin document';
    const pinText = doc.pinned ? 'Unpin' : 'Pin';
    const activeClass = doc.hash === state.activeDocHash ? 'document-item--active' : '';
    const flagCount = doc.flagged_pages || 0;
    const flagBadge = flagCount > 0
      ? `<div class="document-item__flags">${flagCount} flagged</div>`
      : '';
    return `
      <li class="document-item ${activeClass}" data-doc-hash="${doc.hash}">
        <div class="document-item__meta">
          <div class="document-item__name" title="${escapeHtml(doc.name || doc.filename || doc.hash)}">
            ${escapeHtml(doc.name || doc.filename || doc.hash)}
          </div>
          <div class="document-item__status">${escapeHtml(status)}</div>
          ${flagBadge}
        </div>
        <div class="document-item__actions">
          <button
            class="btn btn--pill document-item__pin"
            data-doc-hash="${doc.hash}"
            aria-label="${pinLabel}"
            type="button"
          >
            ${pinText}
          </button>
          <button
            class="btn btn--pill document-item__delete"
            data-doc-hash="${doc.hash}"
            aria-label="Delete document"
            type="button"
          >
            Delete
          </button>
        </div>
      </li>
    `;
  }

  function attachDocumentLoadHandlers() {
    const items = document.querySelectorAll('.document-item');
    items.forEach((item) => {
      item.addEventListener('click', async (event) => {
        if (event.target.closest('.document-item__pin') || event.target.closest('.document-item__delete')) {
          return;
        }
        const hash = item.getAttribute('data-doc-hash');
        if (!hash) {
          return;
        }
        await loadDocumentFromLibrary(hash);
      });
    });
  }

  async function loadDocumentFromLibrary(hash) {
    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/documents/${hash}/file`);
      if (!response.ok) {
        throw new Error('Failed to fetch document');
      }
      const blob = await response.blob();
      const fileName = getDocumentName(hash);
      const file = new File([blob], fileName, { type: 'application/pdf' });
      state.activeDocHash = hash;
      await loadPdfFile(file);
      state.currentDocHash = hash;
      await loadFlaggedPages(hash);
      renderDocumentLists();
      if (state.activeTab === 'pipeline') {
        refreshPipelineStatus();
      }
    } catch (error) {
      console.error('Failed to load document:', error);
    }
  }

  function getDocumentName(hash) {
    const doc = state.documents.find((item) => item.hash === hash);
    return doc ? (doc.name || doc.filename || `${hash}.pdf`) : `${hash}.pdf`;
  }

  function attachPinHandlers() {
    const buttons = document.querySelectorAll('.document-item__pin');
    buttons.forEach((button) => {
      button.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const hash = button.getAttribute('data-doc-hash');
        const doc = state.documents.find((item) => item.hash === hash);
        if (!doc) {
          return;
        }
        await togglePin(hash, !doc.pinned);
      });
    });
  }

  function attachDeleteHandlers() {
    const buttons = document.querySelectorAll('.document-item__delete');
    buttons.forEach((button) => {
      button.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        const hash = button.getAttribute('data-doc-hash');
        if (!hash) {
          return;
        }
        const doc = state.documents.find((item) => item.hash === hash);
        const name = doc ? (doc.name || doc.filename || doc.hash) : hash;
        if (!confirm(`Delete "${name}"? This cannot be undone.`)) {
          return;
        }
        await deleteDocument(hash);
      });
    });
  }

  async function deleteDocument(hash) {
    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/documents/${hash}`, {
        method: 'DELETE',
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to delete document');
      }
      state.documents = state.documents.filter((doc) => doc.hash !== hash);
      if (state.activeDocHash === hash) {
        clearViewerState();
      }
      renderDocumentLists();
    } catch (error) {
      console.error('Delete failed:', error);
    }
  }

  function clearViewerState() {
    state.activeDocHash = null;
    state.currentFile = null;
    state.currentPageNumber = null;
    hideElement(pdfViewer);
    hideElement(pageIndicator);
    pdfPages.innerHTML = '';
    setDropZoneIdle();
    showOutputState('placeholder');
    resetPipelineUI();
  }

  async function togglePin(hash, shouldPin) {
    try {
      const endpoint = shouldPin ? 'pin' : 'unpin';
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/documents/${hash}/${endpoint}`, {
        method: 'POST',
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Failed to update pin');
      }

      state.documents = state.documents.map((doc) => (
        doc.hash === hash ? { ...doc, pinned: shouldPin } : doc
      ));
      renderDocumentLists();
    } catch (error) {
      console.error('Pin update failed:', error);
    }
  }

  function applyFuzzyFilter(documents, query) {
    if (!query) {
      return documents;
    }
    const lowerQuery = query.toLowerCase();
    return documents
      .map((doc) => ({
        doc,
        score: fuzzyScore(doc.name || doc.filename || '', lowerQuery),
      }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((item) => item.doc);
  }

  function fuzzyScore(text, query) {
    const haystack = text.toLowerCase();
    let score = 0;
    let lastIndex = -1;
    for (const char of query) {
      const idx = haystack.indexOf(char, lastIndex + 1);
      if (idx === -1) {
        return 0;
      }
      score += 1;
      if (idx === lastIndex + 1) {
        score += 1;
      }
      lastIndex = idx;
    }
    return score;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  
  /**
   * Populate parser dropdown with available options.
   * 
   * @param {string[]} parsers - Array of parser names
   */
  function populateParserDropdown(parsers) {
    parserSelect.innerHTML = '';
    
    if (parsers && parsers.length > 0) {
      parsers.forEach((parserName) => {
        const option = document.createElement('option');
        option.value = parserName;
        // Convert snake_case to readable label: "Reference_Card" -> "Reference Card"
        option.textContent = parserName.replace(/_/g, ' ');
        parserSelect.appendChild(option);
      });
      scheduleAutoParse();
      scheduleMarkdownFetch();
    } else {
      showParserDropdownError('No parsers available');
    }
  }
  
  /**
   * Show error state in parser dropdown.
   */
  function showParserDropdownError(message) {
    parserSelect.innerHTML = `<option value="">${message}</option>`;
  }
  
  /**
   * Check if auto-parse can run.
   */
  function canAutoParse() {
    return Boolean(state.currentFile && parserSelect.value && state.currentPageNumber);
  }

  function canFetchMarkdown() {
    return Boolean(
      state.markdownVisible &&
      state.currentFile &&
      state.currentPageNumber
    );
  }

  /**
   * Reset auto-parse bookkeeping and cancel pending timers.
   */
  function resetAutoParseState() {
    state.isParsing = false;
    state.pendingParse = false;
    state.lastParsedPage = null;
    state.lastParsedParser = null;
    state.parseNonce += 1;
    state.autoParsePaused = false;
    updatePauseParseButton();

    if (state.parseDebounceId) {
      clearTimeout(state.parseDebounceId);
      state.parseDebounceId = null;
    }
  }

  function resetMarkdownState() {
    state.markdownNonce += 1;
    if (state.markdownDebounceId) {
      clearTimeout(state.markdownDebounceId);
      state.markdownDebounceId = null;
    }
    if (markdownContent) {
      markdownContent.textContent = '';
    }
  }

  function updateMarkdownVisibility() {
    if (!markdownPanel || !markdownToggle) {
      return;
    }
    markdownPanel.classList.toggle('hidden', !state.markdownVisible);
    markdownToggle.setAttribute('aria-pressed', String(state.markdownVisible));
    markdownToggle.textContent = state.markdownVisible ? 'Hide Markdown' : 'Show Markdown';
  }

  /**
   * Debounce auto-parse so quick scrolls don't spam requests.
   */
  function scheduleAutoParse() {
    if (!canAutoParse()) {
      return;
    }

    if (state.autoParsePaused) {
      return;
    }

    if (state.isParsing) {
      state.pendingParse = true;
      return;
    }

    if (state.parseDebounceId) {
      clearTimeout(state.parseDebounceId);
    }

    state.parseDebounceId = setTimeout(() => {
      state.parseDebounceId = null;
      runAutoParse();
    }, CONFIG.PARSE_DEBOUNCE_MS);
  }

  function scheduleMarkdownFetch() {
    if (!canFetchMarkdown()) {
      return;
    }

    if (state.markdownDebounceId) {
      clearTimeout(state.markdownDebounceId);
    }

    state.markdownDebounceId = setTimeout(() => {
      state.markdownDebounceId = null;
      runMarkdownFetch();
    }, CONFIG.PARSE_DEBOUNCE_MS);
  }

  /**
   * Run a parse request for the currently visible page.
   */
  async function runAutoParse() {
    if (!canAutoParse()) {
      return;
    }

    const targetPage = Number(state.currentPageNumber);
    const targetParser = parserSelect.value;

    if (state.lastParsedPage === targetPage && state.lastParsedParser === targetParser) {
      return;
    }

    state.isParsing = true;
    state.pendingParse = false;
    const parseNonce = state.parseNonce + 1;
    state.parseNonce = parseNonce;
    const previousHash = state.currentDocHash;

    showOutputState('loading');

    try {
      console.log('Sending parse request...');
      console.log(targetPage, targetPage + 1, state.currentFile, targetParser);
      const result = await requestPdfParse(state.currentFile, targetParser, {
        start: targetPage,
        end: targetPage + 1,
      });

      if (state.parseNonce !== parseNonce) {
        return;
      }

      if (state.currentDocHash && state.currentDocHash !== previousHash) {
        loadFlaggedPages(state.currentDocHash);
      }

      console.log('Parse result received:', result);
      displayParseResult(result);
      state.lastParsedPage = targetPage;
      state.lastParsedParser = targetParser;
    } catch (error) {
      if (state.parseNonce !== parseNonce) {
        return;
      }

      console.error('Parse error:', error);
      showOutputError(error.message || 'Failed to connect to server');
    } finally {
      if (state.parseNonce === parseNonce) {
        state.isParsing = false;
      }

      if (state.pendingParse) {
        state.pendingParse = false;
        scheduleAutoParse();
      }
    }
  }

  async function runMarkdownFetch() {
    if (!canFetchMarkdown()) {
      return;
    }

    const targetPage = Number(state.currentPageNumber);
    const markdownNonce = state.markdownNonce + 1;
    state.markdownNonce = markdownNonce;

    try {
      const markdown = await requestMarkdown({
        start: targetPage,
        end: targetPage + 1,
      });

      if (state.markdownNonce !== markdownNonce) {
        return;
      }

      if (markdownContent) {
        markdownContent.textContent = markdown || 'No markdown available.';
      }
    } catch (error) {
      if (state.markdownNonce !== markdownNonce) {
        return;
      }

      if (markdownContent) {
        markdownContent.textContent = `Markdown error: ${error.message}`;
      }
    }
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
    if (saveChunksButton) hideElement(saveChunksButton);
    if (flagWrapper) hideElement(flagWrapper);
    if (pauseParseButton) hideElement(pauseParseButton);

    // Only show parse-specific controls when the parse tab is active
    const onParseTab = state.activeTab === 'parse';

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
        if (onParseTab) showElement(copyButton);
        if (onParseTab && saveChunksButton && state.currentDocHash) showElement(saveChunksButton);
        if (onParseTab && flagWrapper) {
          showElement(flagWrapper);
          updateFlagButton();
        }
        if (onParseTab && pauseParseButton) showElement(pauseParseButton);
        break;
    }
  }

  async function loadFlaggedPages(docHash) {
    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/documents/${docHash}/flags`);
      if (!response.ok) return;
      const data = await response.json();
      state.flaggedPages = new Map(
        (data.flagged_pages || []).map((f) => [f.page_number, f.note ?? null])
      );
      updateFlagButton();
      updateFlagNavigation();
    } catch (error) {
      console.error('Failed to load flagged pages:', error);
    }
  }

  function updateFlagButton() {
    if (!flagButton) return;
    const isFlagged = state.flaggedPages.has(state.currentPageNumber);
    flagButton.textContent = isFlagged ? 'Unflag' : 'Flag';
    flagButton.setAttribute('aria-pressed', String(isFlagged));
    flagButton.classList.toggle('flag-active', isFlagged);

    // Set or clear the tooltip text. CSS handles visibility via
    // :hover and :not(:empty) — no class toggling needed.
    if (flagNoteDisplay) {
      const note = isFlagged ? state.flaggedPages.get(state.currentPageNumber) : null;
      flagNoteDisplay.textContent = note || '';
    }
  }
  
  function scrollToPage(pageNum) {
    const canvas = pdfPages.querySelector(`[data-page-num="${pageNum}"]`);
    if (canvas) {
      canvas.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function updateFlagNavigation() {
    if (!flagNav || !flagSelect) return;
    const pages = Array.from(state.flaggedPages.keys()).sort((a, b) => a - b);
    if (pages.length === 0) {
      flagNav.classList.add('hidden');
      return;
    }
    flagNav.classList.remove('hidden');

    flagSelect.innerHTML = '';
    for (const pg of pages) {
      const note = state.flaggedPages.get(pg);
      const label = note ? `Page ${pg} — ${note.substring(0, 40)}` : `Page ${pg}`;
      const opt = document.createElement('option');
      opt.value = pg;
      opt.textContent = label;
      flagSelect.appendChild(opt);
    }

    if (state.currentPageNumber && pages.includes(state.currentPageNumber)) {
      flagSelect.value = state.currentPageNumber;
    }
  }

  function updateFlagNavSelection() {
    if (!flagSelect) return;
    if (state.currentPageNumber && state.flaggedPages.has(state.currentPageNumber)) {
      flagSelect.value = state.currentPageNumber;
    }
  }

  function handleFlagPrev() {
    const pages = Array.from(state.flaggedPages.keys()).sort((a, b) => a - b);
    if (pages.length === 0) return;
    const current = state.currentPageNumber || 0;
    const prev = pages.filter((p) => p < current);
    const target = prev.length > 0 ? prev[prev.length - 1] : pages[pages.length - 1];
    scrollToPage(target);
    if (flagSelect) flagSelect.value = target;
  }

  function handleFlagNext() {
    const pages = Array.from(state.flaggedPages.keys()).sort((a, b) => a - b);
    if (pages.length === 0) return;
    const current = state.currentPageNumber || 0;
    const next = pages.filter((p) => p > current);
    const target = next.length > 0 ? next[0] : pages[0];
    scrollToPage(target);
    if (flagSelect) flagSelect.value = target;
  }

  function handleFlagSelectChange() {
    if (!flagSelect) return;
    const pageNum = Number(flagSelect.value);
    if (pageNum) scrollToPage(pageNum);
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
    console.log('Formatting JSON:', formattedJson.substring(0, 100) + '...');
    
    // Check element existence
    if (!jsonContentElement) {
        console.error('FATAL: jsonContentElement not found in DOM');
        return;
    }

    // Try basic text first to rule out regex issues
    // jsonContentElement.textContent = formattedJson;
    
    const highlightedHtml = applySyntaxHighlighting(formattedJson);
    console.log('Highlighted HTML length:', highlightedHtml.length);
    
    jsonContentElement.innerHTML = highlightedHtml;
    showOutputState('result');
  }
  
  /**
   * Show visual feedback when JSON is copied.
   */
  function showCopyFeedback() {
    const originalContent = copyButton.innerHTML;
    copyButton.textContent = 'Copied';
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
  // Tab Switching
  // ============================================

  function switchTab(tabName) {
    state.activeTab = tabName;

    // Update tab buttons
    if (tabParse) {
      tabParse.classList.toggle('output-tabs__tab--active', tabName === 'parse');
      tabParse.setAttribute('aria-selected', String(tabName === 'parse'));
    }
    if (tabPipeline) {
      tabPipeline.classList.toggle('output-tabs__tab--active', tabName === 'pipeline');
      tabPipeline.setAttribute('aria-selected', String(tabName === 'pipeline'));
    }

    // Update tab panels
    if (tabpanelParse) tabpanelParse.classList.toggle('hidden', tabName !== 'parse');
    if (tabpanelPipeline) tabpanelPipeline.classList.toggle('hidden', tabName !== 'pipeline');

    // Toggle parse-specific toolbar buttons
    const parseOnly = [pauseParseButton, flagWrapper, saveChunksButton, copyButton];
    parseOnly.forEach((el) => {
      if (!el) return;
      if (tabName === 'parse' && !outputJson.classList.contains('hidden')) {
        showElement(el);
      } else if (tabName !== 'parse') {
        hideElement(el);
      }
    });

    // Toggle flag nav visibility
    if (flagNav && tabName !== 'parse') {
      hideElement(flagNav);
    } else if (flagNav && tabName === 'parse' && state.flaggedPages.size > 0) {
      showElement(flagNav);
    }

    // Toggle pipeline-specific toolbar buttons
    if (runAllBtn) {
      if (tabName === 'pipeline' && state.currentDocHash) {
        showElement(runAllBtn);
      } else {
        hideElement(runAllBtn);
      }
    }

    // When switching to Pipeline tab, refresh the status
    if (tabName === 'pipeline' && state.currentDocHash) {
      refreshPipelineStatus();
    }
  }


  // ============================================
  // Pipeline Status & Controls
  // ============================================

  async function refreshPipelineStatus() {
    if (!state.currentDocHash) {
      resetPipelineUI();
      return;
    }

    try {
      const response = await fetch(
        `${CONFIG.API_BASE_URL}/api/graph/status/${state.currentDocHash}`
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || `HTTP ${response.status}`);
      }

      const status = await response.json();
      state.pipelineStatus = status;
      renderPipelineSteps(status);
    } catch (error) {
      console.error('Failed to fetch pipeline status:', error);
      renderPipelineStepsFromDocStatus();
    }
  }

  function renderPipelineSteps(status) {
    const docStatus = status.status;
    const statusOrder = ['uploaded', 'preprocessed', 'parsed', 'entity_extracted', 'graph_staged', 'graph_ready'];
    const currentIndex = statusOrder.indexOf(docStatus);

    statusOrder.forEach((step, i) => {
      const stepEl = document.querySelector(`.pipeline-step[data-step="${step}"]`);
      if (!stepEl) return;

      const indicator = stepEl.querySelector('.pipeline-step__indicator');
      const detail = stepEl.querySelector('.pipeline-step__detail');
      const runBtn = stepEl.querySelector('.pipeline-step__run');

      if (i <= currentIndex) {
        // Completed
        indicator.setAttribute('data-status', 'complete');
        if (step === 'entity_extracted' && status.entity_extracted_at) {
          detail.textContent = formatTimestamp(status.entity_extracted_at);
        } else if (step === 'graph_staged' && status.graph_staged_at) {
          detail.textContent = formatTimestamp(status.graph_staged_at);
        } else if (step === 'graph_ready' && status.graph_ready_at) {
          detail.textContent = formatTimestamp(status.graph_ready_at);
        } else {
          detail.textContent = '';
        }
        if (runBtn) hideElement(runBtn);
      } else if (i === currentIndex + 1) {
        // Next available step
        indicator.setAttribute('data-status', 'pending');
        detail.textContent = 'Ready to run';
        if (runBtn) {
          showElement(runBtn);
          runBtn.disabled = false;
        }
      } else {
        // Locked (prerequisites not met)
        indicator.setAttribute('data-status', 'locked');
        detail.textContent = '';
        if (runBtn) {
          showElement(runBtn);
          runBtn.disabled = true;
        }
      }
    });

    // Show delete button only if graph_ready
    if (pipelineActionsDiv) {
      if (docStatus === 'graph_ready') {
        showElement(pipelineActionsDiv);
      } else {
        hideElement(pipelineActionsDiv);
      }
    }

    // Show progress control when a document is selected
    if (progressControl) {
      showElement(progressControl);
      const doc = state.documents.find((d) => d.hash === state.currentDocHash);
      if (doc && progressInput) {
        progressInput.value = doc.progress || 0;
      }
    }
  }

  function renderPipelineStepsFromDocStatus() {
    const doc = state.documents.find((d) => d.hash === state.currentDocHash);
    if (!doc) return;
    renderPipelineSteps({ status: doc.status || 'uploaded' });
  }

  function resetPipelineUI() {
    document.querySelectorAll('.pipeline-step__indicator').forEach((el) => {
      el.setAttribute('data-status', 'pending');
    });
    document.querySelectorAll('.pipeline-step__detail').forEach((el) => {
      el.textContent = '';
    });
    document.querySelectorAll('.pipeline-step__run').forEach((el) => {
      hideElement(el);
    });
    if (pipelineOutput) hideElement(pipelineOutput);
    if (pipelineErrorDiv) hideElement(pipelineErrorDiv);
    if (pipelineActionsDiv) hideElement(pipelineActionsDiv);
    if (progressControl) hideElement(progressControl);
  }

  function formatTimestamp(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
      });
    } catch {
      return iso;
    }
  }


  // ============================================
  // Pipeline Step Execution
  // ============================================

  async function handlePipelineStepRun(event) {
    const action = event.currentTarget.getAttribute('data-action');
    if (!action || !state.currentDocHash || state.pipelineRunning) return;

    const endpoint = action === 'extract'
      ? '/api/graph/extract'
      : '/api/graph/ingest';

    const stepName = action === 'extract' ? 'entity_extracted' : 'graph_ready';
    const stepEl = document.querySelector(`.pipeline-step[data-step="${stepName}"]`);
    const indicator = stepEl?.querySelector('.pipeline-step__indicator');
    const runBtn = event.currentTarget;

    // UI: show running state
    state.pipelineRunning = true;
    if (indicator) indicator.setAttribute('data-status', 'running');
    runBtn.disabled = true;
    runBtn.textContent = 'Running...';
    hidePipelineError();

    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hash: state.currentDocHash }),
      });

      // Parse response — may be HTML if Apache timed out (502/504)
      let data;
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        throw new Error(
          response.status === 502 || response.status === 504
            ? `Proxy timeout (HTTP ${response.status}). Extraction may still be running — check server logs.`
            : `Server returned non-JSON response (HTTP ${response.status})`
        );
      }

      data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Step failed (HTTP ${response.status})`);
      }

      // Show success
      state.pipelineLastResult = data;
      showPipelineOutput(data, action);

      // Refresh status to update all step indicators
      await refreshPipelineStatus();

      // Also update the document list sidebar (status changed)
      await refreshDocumentList();
    } catch (error) {
      console.error(`Pipeline step ${action} failed:`, error);
      if (indicator) indicator.setAttribute('data-status', 'error');
      showPipelineError(error.message);
    } finally {
      state.pipelineRunning = false;
      runBtn.textContent = 'Run';
      runBtn.disabled = false;
    }
  }

  async function handleRunAll() {
    if (!state.currentDocHash || state.pipelineRunning) return;

    state.pipelineRunning = true;
    if (runAllBtn) {
      runAllBtn.disabled = true;
      runAllBtn.textContent = 'Running...';
    }
    hidePipelineError();

    // Show running state on the next pending step
    const statusOrder = ['uploaded', 'preprocessed', 'parsed', 'entity_extracted', 'graph_staged', 'graph_ready'];
    const doc = state.documents.find((d) => d.hash === state.currentDocHash);
    const currentIndex = statusOrder.indexOf(doc?.status || 'uploaded');
    for (let i = currentIndex + 1; i < statusOrder.length; i++) {
      const stepEl = document.querySelector(`.pipeline-step[data-step="${statusOrder[i]}"]`);
      const indicator = stepEl?.querySelector('.pipeline-step__indicator');
      if (indicator) indicator.setAttribute('data-status', 'running');
    }

    try {
      const response = await fetch(`${CONFIG.API_BASE_URL}/api/graph/pipeline`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hash: state.currentDocHash }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Pipeline failed (HTTP ${response.status})`);
      }

      state.pipelineLastResult = data;
      showPipelineOutput(data, 'pipeline');
      await refreshPipelineStatus();
      await refreshDocumentList();
    } catch (error) {
      console.error('Full pipeline failed:', error);
      showPipelineError(error.message);
      await refreshPipelineStatus();
    } finally {
      state.pipelineRunning = false;
      if (runAllBtn) {
        runAllBtn.textContent = 'Run All';
        runAllBtn.disabled = false;
      }
    }
  }


  // ============================================
  // Pipeline Output Display
  // ============================================

  function showPipelineOutput(data, action) {
    if (!pipelineOutput) return;
    showElement(pipelineOutput);

    // Build summary stats based on action type
    let stats = [];
    if (action === 'extract') {
      stats = [
        { label: 'Entities', value: data.entities_found ?? '—' },
        { label: 'Relationships', value: data.relationships_found ?? '—' },
        { label: 'Chunks', value: data.chunks_processed ?? '—' },
        { label: 'Errors', value: data.chunks_with_errors ?? 0 },
      ];
    } else if (action === 'ingest') {
      const r = data.result || {};
      stats = [
        { label: 'Structural Nodes', value: r.structural?.nodes_created ?? '—' },
        { label: 'Structural Edges', value: r.structural?.edges_created ?? '—' },
        { label: 'Semantic Entities', value: r.semantic?.entities ?? '—' },
        { label: 'Semantic Rels', value: r.semantic?.relationships ?? '—' },
      ];
    } else if (action === 'pipeline') {
      const r = data.result || {};
      stats = [{ label: 'Status', value: 'Complete' }];
      if (r.extraction) {
        stats.push(
          { label: 'Entities', value: r.extraction.entities?.length ?? '—' },
          { label: 'Relationships', value: r.extraction.relationships?.length ?? '—' },
        );
      }
      if (r.ingestion) {
        stats.push(
          { label: 'Graph Nodes', value: r.ingestion.structural?.nodes_created ?? '—' },
          { label: 'Graph Edges', value: r.ingestion.structural?.edges_created ?? '—' },
        );
      }
    }

    if (pipelineOutputSummary) {
      pipelineOutputSummary.innerHTML = stats.map((s) => `
        <div class="pipeline-stat">
          <div class="pipeline-stat__value">${escapeHtml(String(s.value))}</div>
          <div class="pipeline-stat__label">${escapeHtml(s.label)}</div>
        </div>
      `).join('');
    }

    // Prepare the JSON for toggle
    if (pipelineJsonContent) {
      const json = JSON.stringify(data, null, 2);
      pipelineJsonContent.innerHTML = applySyntaxHighlighting(json);
    }
    if (pipelineOutputJson) hideElement(pipelineOutputJson);
    if (pipelineOutputToggle) pipelineOutputToggle.textContent = 'Show JSON';
  }

  function handlePipelineJsonToggle() {
    if (!pipelineOutputJson) return;
    const isHidden = pipelineOutputJson.classList.contains('hidden');
    pipelineOutputJson.classList.toggle('hidden', !isHidden);
    if (pipelineOutputToggle) {
      pipelineOutputToggle.textContent = isHidden ? 'Hide JSON' : 'Show JSON';
    }
  }

  function showPipelineError(message) {
    if (!pipelineErrorDiv || !pipelineErrorMessage) return;
    pipelineErrorMessage.textContent = message;
    showElement(pipelineErrorDiv);
  }

  function hidePipelineError() {
    if (pipelineErrorDiv) hideElement(pipelineErrorDiv);
  }

  async function handleDeleteGraph() {
    if (!state.currentDocHash) return;
    if (!confirm('Delete graph from Neo4j? This cannot be undone.')) return;

    if (deleteGraphBtn) {
      deleteGraphBtn.disabled = true;
      deleteGraphBtn.textContent = 'Deleting...';
    }

    try {
      const response = await fetch(
        `${CONFIG.API_BASE_URL}/api/graph/${state.currentDocHash}`,
        { method: 'DELETE' }
      );

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || 'Delete failed');
      }

      if (deleteGraphBtn) {
        deleteGraphBtn.textContent = `Deleted (${data.nodes_deleted} nodes)`;
        setTimeout(() => {
          deleteGraphBtn.textContent = 'Delete Graph';
          deleteGraphBtn.disabled = false;
        }, 2000);
      }

      await refreshPipelineStatus();
      await refreshDocumentList();
    } catch (error) {
      console.error('Delete graph failed:', error);
      showPipelineError(error.message);
      if (deleteGraphBtn) {
        deleteGraphBtn.textContent = 'Delete Graph';
        deleteGraphBtn.disabled = false;
      }
    }
  }


  async function handleSetProgress() {
    if (!state.currentDocHash) return;
    const value = Math.max(0, Math.min(100, parseInt(progressInput.value, 10) || 0));
    progressInput.value = value;

    try {
      progressSetBtn.disabled = true;
      progressSetBtn.textContent = 'Saving...';

      const response = await fetch(
        `${CONFIG.API_BASE_URL}/api/documents/${state.currentDocHash}/progress`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ progress: value }),
        }
      );

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || 'Failed to set progress');
      }

      progressSetBtn.textContent = 'Saved!';
      setTimeout(() => {
        progressSetBtn.textContent = 'Set';
        progressSetBtn.disabled = false;
      }, 1500);

      await refreshDocumentList();
    } catch (error) {
      console.error('Set progress failed:', error);
      progressSetBtn.textContent = 'Set';
      progressSetBtn.disabled = false;
    }
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
   * Format a file size in bytes to a human-readable string.
   */
  function formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return '0 B';
    }
  
    const units = ['B', 'KB', 'MB', 'GB'];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, index);
  
    return `${value.toFixed(value < 10 && index > 0 ? 1 : 0)} ${units[index]}`;
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

      // For string values (not keys), render escaped newlines as real
      // line breaks so chunk content mimics the document structure.
      // The <pre> block's white-space:pre-wrap handles the rendering.
      if (tokenClass === 'json-string') {
        match = match.replace(/\\n/g, '\n');
      }

      return `<span class="${tokenClass}">${match}</span>`;
    });
  }
