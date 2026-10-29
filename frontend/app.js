/**
 * ResonanceAI - Frontend Application Controller
 * High-fidelity implementation of Figma designs for Web and Mobile App views.
 */

import { ResonanceAPI } from './api.js';

class ResonanceApp {
  constructor() {
    this.api = new ResonanceAPI();
    
    // Application State
    this.state = {
      viewMode: 'desktop', // 'desktop' | 'mobile'
      currentScreen: 'dashboard', // 'welcome' | 'signup' | 'dashboard' | 'tutor' | 'steps' | 'policy' | 'settings' | 'viva'
      theme: 'light',
      activeAssignmentId: 'demo-green',
      assignments: [],
      user: {
        name: 'Ananya Sharma',
        email: 'ananya.sharma@iit.ac.in',
        studentId: '2024CSB1042',
        institution: 'Indian Institute of Technology Delhi',
        department: 'Computer Science & Engineering',
        year: 'Year 3 (Semester 5)',
        role: 'Student',
      },
      chatMessages: [
        {
          id: 'msg-1',
          sender: 'student',
          time: '10:42 AM',
          text: "I'm stuck on recursion for binary trees. How do I know when the base case should return 0 vs None?",
          attachment: null,
        },
        {
          id: 'msg-2',
          sender: 'tutor',
          time: '10:43 AM',
          headline: "Recursion is a smaller version of the same problem.",
          body: "When approaching tree recursion, the base case represents the boundary condition when you can go no further — specifically, an empty subtree (node is None). The return value depends entirely on what your recursive contract expects:",
          cards: [
            {
              title: "Base Case: Return 0",
              subtitle: "When computing scalar values like count, tree depth, node sum, or path cost.",
              code: "if not node:\n    return 0",
            },
            {
              title: "Base Case: Return None",
              subtitle: "When constructing, modifying, or returning a node pointer or subtree reference.",
              code: "if not node:\n    return None",
            },
          ],
          reflectiveQuestion: "What question does your recursive function need to answer for an empty leaf in this problem?",
          stepLabel: "Step 1 of 3: Identify Return Type",
          actionTaken: "EXPLAIN_CONCEPT",
          intent: "CONCEPT_EXPLANATION",
        },
      ],
      currentStep: 1,
      disclosureAcknowledged: false,
      selectedLanguage: 'en',
      socraticDepth: 'guided',
      isGenerating: false,
      attachedFile: null,
      settings: {
        pseudonymLogs: true,
        indiaResidency: true,
        auditChaining: true,
      },
    };

    this.init();
  }

  async init() {
    this.bindGlobalEvents();
    this.applyTheme(this.state.theme);
    this.updateClock();
    setInterval(() => this.updateClock(), 30000);

    // Initial backend fetch
    try {
      this.state.assignments = await this.api.getAssignments();
      const readyState = await this.api.checkReady();
      const elPlaceholder = document.getElementById('env-placeholder-val');
      if (elPlaceholder && readyState.dependencies) {
        elPlaceholder.textContent = readyState.dependencies.local_placeholder || 'enabled';
      }
    } catch (e) {
      console.warn("Using offline fixtures:", e);
    }

    this.render();
  }

  // ========================================================================
  // Global Event Binding
  // ========================================================================
  bindGlobalEvents() {
    // Mode Switcher (Desktop ↔ Mobile)
    const btnDesktop = document.getElementById('view-mode-desktop');
    const btnMobile = document.getElementById('view-mode-mobile');
    
    btnDesktop?.addEventListener('click', () => this.setViewMode('desktop'));
    btnMobile?.addEventListener('click', () => this.setViewMode('mobile'));

    // Theme Toggle
    document.getElementById('btn-theme-toggle')?.addEventListener('click', () => {
      this.toggleTheme();
    });

    // Navigation Items
    document.querySelectorAll('[data-nav]').forEach(el => {
      el.addEventListener('click', (e) => {
        const screen = e.currentTarget.getAttribute('data-nav');
        this.navigateTo(screen);
      });
    });

    // Brand click -> Dashboard
    document.getElementById('btn-brand')?.addEventListener('click', () => {
      this.navigateTo('dashboard');
    });

    // Start New Inquiry Button
    document.getElementById('btn-new-inquiry')?.addEventListener('click', () => {
      this.state.activeAssignmentId = 'demo-green';
      this.navigateTo('tutor');
    });

    // Switch to Welcome / Sign-in screen demo
    document.getElementById('btn-show-welcome-screen')?.addEventListener('click', () => {
      this.navigateTo('welcome');
    });

    // Endpoints Inspector Drawer
    const drawer = document.getElementById('inspector-drawer');
    document.getElementById('btn-open-inspector')?.addEventListener('click', () => {
      drawer?.classList.remove('translate-x-full');
    });
    document.getElementById('btn-close-inspector')?.addEventListener('click', () => {
      drawer?.classList.add('translate-x-full');
    });

    // API Inspector Pings
    document.getElementById('ping-health')?.addEventListener('click', async () => {
      const res = await this.api.checkHealth();
      this.showToast(res.ok ? "Health Check: OK (200)" : "Health: Offline", res.ok ? "success" : "error");
    });
    document.getElementById('ping-ready')?.addEventListener('click', async () => {
      const res = await this.api.checkReady();
      this.showToast(`Ready Check: ${res.status}`, "info");
    });
    document.getElementById('ping-tutor')?.addEventListener('click', async () => {
      this.showToast("Testing /dev/tutor/chat...", "info");
      const res = await this.api.sendTutorMessage({
        assignmentId: 'demo-green',
        message: 'Explain recursion base cases',
      });
      this.showToast(`Tutor API Response: ${res.data.status || 'OK'}`, "success");
    });
    document.getElementById('ping-preprocess')?.addEventListener('click', async () => {
      this.showToast("Testing /preprocess OCR & normalizer...", "info");
      const res = await this.api.preprocessInput({
        text: 'How do binary trees work?',
        assignmentId: 'demo-green',
      });
      this.showToast(`Preprocess: Policy ${res.data.policy?.policy_flag || 'OK'}`, "success");
    });

    document.getElementById('btn-clear-logs')?.addEventListener('click', () => {
      this.api.logs = [];
      this.renderInspectorLogs();
    });

    this.api.onLog(() => this.renderInspectorLogs());

    // Disclose Modal Events
    document.getElementById('btn-close-disclose')?.addEventListener('click', () => this.hideModal('modal-disclose'));
    document.getElementById('btn-cancel-disclose')?.addEventListener('click', () => this.hideModal('modal-disclose'));
    document.getElementById('btn-submit-disclose')?.addEventListener('click', () => this.handleDisclosureSubmit());

    // Receipt Modal Events
    document.getElementById('btn-copy-receipt-hash')?.addEventListener('click', () => {
      const hash = document.getElementById('receipt-hash')?.textContent || '';
      navigator.clipboard.writeText(hash);
      const btnText = document.getElementById('btn-copy-text');
      if (btnText) btnText.textContent = "Copied to Clipboard!";
      setTimeout(() => { if (btnText) btnText.textContent = "Copy Verification Receipt"; }, 2000);
    });

    document.getElementById('btn-continue-viva-prep')?.addEventListener('click', () => {
      this.hideModal('modal-receipt');
      this.navigateTo('viva');
    });
  }

  // ========================================================================
  // Viewport & Mode Management
  // ========================================================================
  setViewMode(mode) {
    this.state.viewMode = mode;
    const desktopContainer = document.getElementById('desktop-container');
    const mobileContainer = document.getElementById('mobile-container');
    const btnDesktop = document.getElementById('view-mode-desktop');
    const btnMobile = document.getElementById('view-mode-mobile');

    if (mode === 'mobile') {
      desktopContainer?.classList.add('hidden');
      mobileContainer?.classList.remove('hidden');

      btnMobile?.classList.add('bg-[var(--bg-surface)]', 'text-[var(--text-primary)]', 'shadow-sm');
      btnMobile?.classList.remove('text-[var(--text-secondary)]');
      btnDesktop?.classList.remove('bg-[var(--bg-surface)]', 'text-[var(--text-primary)]', 'shadow-sm');
      btnDesktop?.classList.add('text-[var(--text-secondary)]');
    } else {
      mobileContainer?.classList.add('hidden');
      desktopContainer?.classList.remove('hidden');

      btnDesktop?.classList.add('bg-[var(--bg-surface)]', 'text-[var(--text-primary)]', 'shadow-sm');
      btnDesktop?.classList.remove('text-[var(--text-secondary)]');
      btnMobile?.classList.remove('bg-[var(--bg-surface)]', 'text-[var(--text-primary)]', 'shadow-sm');
      btnMobile?.classList.add('text-[var(--text-secondary)]');
    }

    this.render();
  }

  toggleTheme() {
    this.state.theme = this.state.theme === 'light' ? 'dark' : 'light';
    this.applyTheme(this.state.theme);
  }

  applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const sunIcon = document.getElementById('icon-sun');
    const moonIcon = document.getElementById('icon-moon');
    if (theme === 'dark') {
      sunIcon?.classList.remove('hidden');
      moonIcon?.classList.add('hidden');
    } else {
      sunIcon?.classList.add('hidden');
      moonIcon?.classList.remove('hidden');
    }
  }

  updateClock() {
    const now = new Date();
    const hours = now.getHours();
    const minutes = now.getMinutes().toString().padStart(2, '0');
    const elClock = document.getElementById('mobile-clock');
    if (elClock) elClock.textContent = `${hours}:${minutes}`;
  }

  navigateTo(screen) {
    this.state.currentScreen = screen;
    // Update active navbar styles
    document.querySelectorAll('.nav-item').forEach(el => {
      const match = el.getAttribute('data-nav') === screen;
      el.classList.toggle('active', match);
      el.classList.toggle('bg-[var(--sage-primary-light)]', match);
      el.classList.toggle('text-[var(--sage-primary)]', match);
      el.classList.toggle('text-[var(--text-secondary)]', !match);
    });

    document.querySelectorAll('.mobile-tab-item').forEach(el => {
      const match = el.getAttribute('data-nav') === screen;
      el.classList.toggle('active', match);
      el.classList.toggle('text-[var(--sage-primary)]', match);
      el.classList.toggle('text-[var(--text-muted)]', !match);
    });

    this.render();
  }

  showModal(id) {
    document.getElementById(id)?.classList.remove('hidden');
  }

  hideModal(id) {
    document.getElementById(id)?.classList.add('hidden');
  }

  showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `p-3 px-4 rounded-xl text-xs font-medium shadow-lg pointer-events-auto flex items-center gap-2 transform transition-all duration-300 translate-y-2 opacity-0 ${
      type === 'success'
        ? 'bg-emerald-800 text-white border border-emerald-700'
        : type === 'error'
        ? 'bg-red-800 text-white border border-red-700'
        : 'bg-[var(--bg-surface)] text-[var(--text-primary)] border border-[var(--border-subtle)]'
    }`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.remove('translate-y-2', 'opacity-0');
    });

    setTimeout(() => {
      toast.classList.add('opacity-0', 'translate-y-2');
      setTimeout(() => toast.remove(), 300);
    }, 3200);
  }

  // ========================================================================
  // Main Renderer: Renders target screen into desktop or mobile mounts
  // ========================================================================
  render() {
    const desktopMount = document.getElementById('screen-mount');
    const mobileMount = document.getElementById('mobile-screen-mount');

    const html = this.getScreenHTML(this.state.currentScreen);

    if (this.state.viewMode === 'desktop') {
      if (desktopMount) {
        desktopMount.innerHTML = html;
        this.attachScreenEvents(desktopMount);
      }
    } else {
      if (mobileMount) {
        mobileMount.innerHTML = html;
        this.attachScreenEvents(mobileMount);
      }
    }
  }

  getScreenHTML(screen) {
    switch (screen) {
      case 'welcome':
        return this.renderWelcomeScreen();
      case 'signup':
        return this.renderSignupScreen();
      case 'dashboard':
        return this.renderDashboardScreen();
      case 'tutor':
        return this.renderTutorScreen();
      case 'steps':
        return this.renderStepsScreen();
      case 'policy':
        return this.renderPolicyScreen();
      case 'settings':
        return this.renderSettingsScreen();
      case 'viva':
        return this.renderVivaScreen();
      default:
        return this.renderDashboardScreen();
    }
  }

  // ========================================================================
  // Screen 1 & 10: Welcome / Sign In (Hero with Cascading Wireframes)
  // ========================================================================
  renderWelcomeScreen() {
    return `
      <div class="relative min-h-[580px] rounded-3xl overflow-hidden bg-gradient-to-br from-[#627766] to-[#4A5D4D] text-white p-6 sm:p-12 flex items-center justify-center shadow-xl">
        <!-- Cascading Angled Wireframe Motif from Figma Screen 1 -->
        <div class="wireframe-stack">
          <div class="wireframe-box wireframe-box-1"></div>
          <div class="wireframe-box wireframe-box-2"></div>
          <div class="wireframe-box wireframe-box-3"></div>
          <div class="wireframe-box wireframe-box-4"></div>
        </div>

        <div class="relative z-10 w-full max-w-md bg-[var(--bg-surface)] text-[var(--text-primary)] rounded-2xl p-7 shadow-2xl border border-[var(--border-subtle)] space-y-6 animate-fade-in">
          <div>
            <span class="text-[10px] font-mono uppercase tracking-widest text-[var(--sage-primary)] font-semibold">WELCOME BACK</span>
            <h2 class="font-serif text-3xl font-normal text-[var(--text-primary)] tracking-tight mt-1">Make room for your next aha.</h2>
            <p class="text-xs text-[var(--text-secondary)] mt-1.5 leading-relaxed">Log in with your institutional credentials to enter your Socratic workspace.</p>
          </div>

          <form id="form-signin" class="space-y-4">
            <div class="space-y-1">
              <label class="block text-xs font-medium text-[var(--text-secondary)]">Institutional Email or Student ID</label>
              <input type="text" id="input-signin-email" value="ananya.sharma@iit.ac.in" required
                class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--sage-primary)] transition-colors">
            </div>

            <div class="space-y-1">
              <div class="flex justify-between items-center text-xs">
                <label class="font-medium text-[var(--text-secondary)]">Password</label>
                <a href="#" class="text-[var(--sage-primary)] hover:underline text-[11px]">Forgot password?</a>
              </div>
              <input type="password" id="input-signin-password" value="••••••••••••" required
                class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--sage-primary)] transition-colors">
            </div>

            <div class="flex items-center justify-between text-xs text-[var(--text-secondary)]">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked class="rounded text-[var(--sage-primary)] focus:ring-0">
                <span>Remember this workstation</span>
              </label>
            </div>

            <button type="submit" class="w-full py-3 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white font-medium text-xs shadow-md transition-all transform active:scale-98">
              Sign In to Workspace
            </button>
          </form>

          <div class="pt-3 border-t border-[var(--border-subtle)] text-center text-xs text-[var(--text-secondary)]">
            <span>New student or faculty? </span>
            <button id="btn-goto-signup" class="text-[var(--sage-primary)] font-semibold hover:underline">Create an account</button>
          </div>

          <div class="pt-2 text-[10px] font-mono text-[var(--text-muted)] text-center flex items-center justify-center gap-1.5">
            <svg class="w-3 h-3 text-emerald-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            <span>Tenant RLS isolation • ap-south-1 residency</span>
          </div>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen 2: Sign Up (Create Account)
  // ========================================================================
  renderSignupScreen() {
    return `
      <div class="max-w-md mx-auto bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-7 shadow-xl space-y-6 animate-fade-in my-6">
        <div>
          <span class="text-[10px] font-mono uppercase tracking-widest text-[var(--sage-primary)] font-semibold">INSTITUTION ONBOARDING</span>
          <h2 class="font-serif text-3xl font-normal text-[var(--text-primary)] tracking-tight mt-1">Create your account</h2>
          <p class="text-xs text-[var(--text-secondary)] mt-1.5">Connect your academic institution for verified Socratic tutoring.</p>
        </div>

        <form id="form-signup" class="space-y-4">
          <div class="space-y-1">
            <label class="block text-xs font-medium text-[var(--text-secondary)]">Full Name</label>
            <input type="text" value="Ananya Sharma" required class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)]">
          </div>
          <div class="space-y-1">
            <label class="block text-xs font-medium text-[var(--text-secondary)]">Institutional Email (.ac.in / .edu)</label>
            <input type="email" value="ananya.sharma@iit.ac.in" required class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)]">
          </div>
          <div class="space-y-1">
            <label class="block text-xs font-medium text-[var(--text-secondary)]">Academic Department & Code</label>
            <input type="text" value="Computer Science (IIT Delhi - IITD-CS-2026)" required class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)]">
          </div>
          <div class="space-y-1">
            <label class="block text-xs font-medium text-[var(--text-secondary)]">Password</label>
            <input type="password" value="pass123456" required class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)]">
          </div>

          <label class="flex items-start gap-2.5 text-xs text-[var(--text-secondary)] cursor-pointer">
            <input type="checkbox" checked class="mt-0.5 rounded text-[var(--sage-primary)]">
            <span>I agree to the Academic Honor Code, Process Log Anonymization, and ap-south-1 Data Residency policy (NFR-PRV-05).</span>
          </label>

          <button type="submit" class="w-full py-3 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white font-medium text-xs shadow-md">
            Create Account & Enter Workspace
          </button>
        </form>

        <div class="pt-2 border-t border-[var(--border-subtle)] text-center text-xs text-[var(--text-secondary)]">
          <span>Already enrolled? </span>
          <button id="btn-goto-signin" class="text-[var(--sage-primary)] font-semibold hover:underline">Sign in</button>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen 4: Student Dashboard ("Good afternoon, Ananya.")
  // ========================================================================
  renderDashboardScreen() {
    return `
      <div class="space-y-7 animate-fade-in">
        <!-- Dashboard Header -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div class="text-[11px] font-mono uppercase tracking-wider text-[var(--text-muted)]">STUDENT WORKSPACE • IIT DELHI</div>
            <h1 class="font-serif text-3xl sm:text-4xl font-normal text-[var(--text-primary)] tracking-tight mt-0.5">Good afternoon, Ananya.</h1>
            <p class="text-xs text-[var(--text-secondary)] mt-1">Here is your active learning workspace and assignment policy status.</p>
          </div>
          <div class="flex items-center gap-2">
            <button id="btn-dash-viva" class="px-4 py-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-subtle)] text-xs font-medium flex items-center gap-2 shadow-sm transition-colors">
              <svg class="w-4 h-4 text-[var(--sage-primary)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>
              <span>Viva Schedule</span>
            </button>
            <button id="btn-dash-inquiry" class="px-4 py-2 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium flex items-center gap-2 shadow-sm transition-colors">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              <span>Ask Socratic Tutor</span>
            </button>
          </div>
        </div>

        <!-- Upcoming Viva Banner (Figma Screen 4) -->
        <div class="p-5 rounded-2xl bg-gradient-to-r from-[#4E6652] to-[#3B503E] text-white shadow-md flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div class="flex items-start gap-4">
            <div class="w-11 h-11 rounded-xl bg-white/15 flex items-center justify-center shrink-0 border border-white/20">
              <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>
            </div>
            <div>
              <div class="flex items-center gap-2">
                <span class="text-[10px] font-mono uppercase tracking-wider bg-white/20 px-2 py-0.5 rounded-full font-semibold">ORAL DEFENSE</span>
                <span class="text-xs text-white/80">Scheduled in 2 days</span>
              </div>
              <h3 class="font-serif text-lg font-medium text-white mt-0.5">Upcoming viva: Binary Search Trees & Recursion</h3>
              <p class="text-xs text-white/70">Prof. R. Sengupta • Scheduled for Nov 2, 2:30 PM IST</p>
            </div>
          </div>
          <button id="btn-practice-viva-banner" class="px-4 py-2.5 rounded-xl bg-white text-[#2D3F31] font-semibold text-xs shadow hover:bg-zinc-100 transition-colors shrink-0">
            Practice Socratic Viva
          </button>
        </div>

        <!-- Filter Tabs -->
        <div class="flex items-center justify-between border-b border-[var(--border-subtle)] pb-2 text-xs">
          <div class="flex items-center gap-3">
            <button class="font-medium text-[var(--sage-primary)] border-b-2 border-[var(--sage-primary)] pb-2 px-1">All Assignments (3)</button>
            <button class="text-[var(--text-muted)] hover:text-[var(--text-primary)] pb-2 px-1">In Progress (2)</button>
            <button class="text-[var(--text-muted)] hover:text-[var(--text-primary)] pb-2 px-1">Completed (1)</button>
          </div>
          <span class="text-[11px] font-mono text-[var(--text-muted)] hidden sm:inline">Authoritative Policy Guard Active</span>
        </div>

        <!-- 3 Assignment Cards (Green, Yellow, Red) -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-5">
          <!-- Card 1: Green (Full Integration) -->
          <div class="p-5 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] transition-all flex flex-col justify-between shadow-sm space-y-4">
            <div class="space-y-3">
              <div class="flex items-center justify-between">
                <span class="text-[11px] font-mono text-[var(--text-muted)]">CS201 • ASSIGNMENT 04</span>
                <span class="badge-policy-green text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-emerald-600"></span>
                  FULL INTEGRATION
                </span>
              </div>
              <div>
                <h3 class="font-serif text-lg font-normal text-[var(--text-primary)]">Recursion & Binary Search Trees</h3>
                <p class="text-xs text-[var(--text-secondary)] mt-1.5 leading-relaxed">Socratic AI tutoring is fully enabled. Ask conceptual questions, explore edge cases, and receive guided stepped hints.</p>
              </div>
            </div>

            <div class="space-y-3 pt-3 border-t border-[var(--border-subtle)]">
              <div class="flex justify-between items-center text-[11px] text-[var(--text-muted)]">
                <span>Progress: Step 1 of 3</span>
                <span>Due in 6 days</span>
              </div>
              <button data-open-assignment="demo-green" class="btn-open-workspace w-full py-2.5 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium transition-colors shadow-sm">
                Open Workspace
              </button>
            </div>
          </div>

          <!-- Card 2: Yellow (Process Disclosure) -->
          <div class="p-5 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] transition-all flex flex-col justify-between shadow-sm space-y-4">
            <div class="space-y-3">
              <div class="flex items-center justify-between">
                <span class="text-[11px] font-mono text-[var(--text-muted)]">CS202 • ASSIGNMENT 03</span>
                <span class="badge-policy-yellow text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-amber-600"></span>
                  PROCESS DISCLOSURE
                </span>
              </div>
              <div>
                <h3 class="font-serif text-lg font-normal text-[var(--text-primary)]">Graph Traversal & Shortest Path</h3>
                <p class="text-xs text-[var(--text-secondary)] mt-1.5 leading-relaxed">AI assistance is permitted with mandatory disclosure of queries and reflections upon final submission.</p>
              </div>
            </div>

            <div class="space-y-3 pt-3 border-t border-[var(--border-subtle)]">
              <div class="flex justify-between items-center text-[11px] text-[var(--text-muted)]">
                <span>Disclosure pending</span>
                <span>Due in 13 days</span>
              </div>
              <button data-open-assignment="demo-yellow" class="btn-open-workspace w-full py-2.5 rounded-xl border border-[var(--border-strong)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-subtle)] text-[var(--text-primary)] text-xs font-medium transition-colors shadow-sm">
                Open Workspace & Disclose
              </button>
            </div>
          </div>

          <!-- Card 3: Red (No AI / Strict Honor Code) -->
          <div class="p-5 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] hover:border-[var(--border-strong)] transition-all flex flex-col justify-between shadow-sm space-y-4">
            <div class="space-y-3">
              <div class="flex items-center justify-between">
                <span class="text-[11px] font-mono text-[var(--text-muted)]">CS304 • FINAL PROJECT</span>
                <span class="badge-policy-red text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-red-600"></span>
                  NO AI ALLOWED
                </span>
              </div>
              <div>
                <h3 class="font-serif text-lg font-normal text-[var(--text-primary)]">Core Cryptographic Primitives</h3>
                <p class="text-xs text-[var(--text-secondary)] mt-1.5 leading-relaxed">Generative AI assistance is strictly barred for this assessment. Fail-closed policy enforcement active.</p>
              </div>
            </div>

            <div class="space-y-3 pt-3 border-t border-[var(--border-subtle)]">
              <div class="flex justify-between items-center text-[11px] text-[var(--text-muted)]">
                <span>Independent submission only</span>
                <span>Due in 21 days</span>
              </div>
              <button data-open-assignment="demo-red" class="btn-open-workspace w-full py-2.5 rounded-xl border border-[var(--policy-red-border)] bg-[var(--policy-red-bg)] text-[var(--policy-red-text)] hover:opacity-90 text-xs font-medium transition-opacity">
                View Policy & Honor Code
              </button>
            </div>
          </div>
        </div>

        <!-- Footer Isolation & Security Note -->
        <div class="p-4 rounded-xl bg-[var(--bg-surface-subtle)] border border-[var(--border-subtle)] flex items-center justify-between text-xs text-[var(--text-muted)]">
          <div class="flex items-center gap-2">
            <svg class="w-4 h-4 text-emerald-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            <span>Tenant-isolated storage active. Process logs are pseudonymized before persistence in compliance with NFR-PRV-05 (ap-south-1).</span>
          </div>
          <span class="font-mono text-[11px]">Audit: append-only</span>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen 5 & 11: Socratic Tutoring Workspace
  // ========================================================================
  renderTutorScreen() {
    const aid = this.state.activeAssignmentId;
    const assignment = this.state.assignments.find(a => a.assignment_id === aid) || {
      assignment_id: 'demo-green',
      title: 'Recursion & Binary Search Trees',
      course_code: 'CS201',
      policy_level: 'GREEN',
      policy_name: 'FULL INTEGRATION',
    };

    const isYellow = assignment.policy_level === 'YELLOW';
    const isRed = assignment.policy_level === 'RED';

    return `
      <div class="space-y-5 animate-fade-in flex flex-col h-[calc(100vh-140px)]">
        <!-- Tutor Top Header -->
        <div class="p-4 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-3 shrink-0 shadow-sm">
          <div class="flex items-center gap-3">
            <button id="btn-back-dashboard" class="p-1.5 rounded-lg hover:bg-[var(--bg-surface-subtle)] text-[var(--text-secondary)]">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
            </button>
            <div>
              <div class="flex items-center gap-2">
                <span class="text-xs font-mono font-medium text-[var(--text-muted)]">${assignment.course_code}</span>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded-full ${
                  isRed ? 'badge-policy-red' : (isYellow ? 'badge-policy-yellow' : 'badge-policy-green')
                }">
                  ${assignment.policy_name}
                </span>
              </div>
              <h2 class="font-serif text-xl font-normal text-[var(--text-primary)] mt-0.5">${assignment.title}</h2>
            </div>
          </div>

          <!-- Language Selector & Deposit Action -->
          <div class="flex items-center gap-2.5">
            <!-- Multilingual selector -->
            <select id="select-tutor-language" class="text-xs font-mono py-1.5 px-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--sage-primary)]">
              <option value="en" ${this.state.selectedLanguage === 'en' ? 'selected' : ''}>English (EN)</option>
              <option value="hi" ${this.state.selectedLanguage === 'hi' ? 'selected' : ''}>Hindi (हिन्दी)</option>
              <option value="ta" ${this.state.selectedLanguage === 'ta' ? 'selected' : ''}>Tamil (தமிழ்)</option>
              <option value="te" ${this.state.selectedLanguage === 'te' ? 'selected' : ''}>Telugu (తెలుగు)</option>
            </select>

            ${isYellow ? `
              <button id="btn-open-disclose-modal" class="px-3.5 py-1.5 rounded-xl bg-amber-600 hover:bg-amber-700 text-white text-xs font-medium shadow-sm flex items-center gap-1.5">
                <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                <span>Deposit & Disclose</span>
              </button>
            ` : `
              <button id="btn-open-disclose-modal" class="px-3.5 py-1.5 rounded-xl border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-subtle)] text-xs font-medium text-[var(--text-secondary)]">
                Attach Reflection
              </button>
            `}
          </div>
        </div>

        <!-- Chat History Stream -->
        <div id="tutor-chat-stream" class="flex-1 overflow-y-auto space-y-5 p-4 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)]">
          ${this.state.chatMessages.map(msg => this.renderChatMessage(msg)).join('')}
          ${this.state.isGenerating ? `
            <div class="p-4 rounded-xl bg-[var(--bg-surface-subtle)] border border-[var(--border-subtle)] text-xs font-mono flex items-center gap-3 animate-pulse">
              <div class="w-3 h-3 rounded-full bg-[var(--sage-primary)] animate-ping"></div>
              <span>Socratic orchestrator reasoning through pedagogical policy...</span>
            </div>
          ` : ''}
        </div>

        <!-- Multimodal Input Seam & Socratic Prompt Input -->
        <div class="p-3.5 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-sm space-y-2.5 shrink-0">
          <!-- Attachment Chip if present -->
          <div id="attachment-preview" class="hidden items-center justify-between p-2 rounded-lg bg-[var(--bg-app)] border border-[var(--border-subtle)] text-xs">
            <div class="flex items-center gap-2 text-[var(--text-secondary)]">
              <svg class="w-4 h-4 text-emerald-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>
              <span id="attachment-name" class="font-mono">notes_diagram.png</span>
              <span class="text-[10px] text-[var(--text-muted)]">(Multipart /preprocess ready)</span>
            </div>
            <button id="btn-remove-attachment" class="text-[var(--text-muted)] hover:text-red-500">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>

          <div class="flex items-center gap-2">
            <!-- Hidden file input for Multimodal OCR / Notes -->
            <input type="file" id="file-multimodal" accept="image/png,image/jpeg,image/webp" class="hidden">
            <button id="btn-trigger-upload" class="p-2.5 rounded-xl border border-[var(--border-subtle)] hover:bg-[var(--bg-surface-subtle)] text-[var(--text-secondary)]" title="Upload handwritten notes or tree diagram (OCR via /preprocess)">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
            </button>

            <!-- Text Prompt Input -->
            <input type="text" id="input-tutor-message" placeholder="Ask a conceptual question, request a hint, or paste your pseudocode..."
              class="flex-1 text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--sage-primary)] transition-colors">

            <!-- Send Action -->
            <button id="btn-send-tutor" class="px-5 py-3 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium shadow-sm flex items-center gap-1.5 transition-colors">
              <span>Send</span>
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
            </button>
          </div>

          <div class="flex items-center justify-between text-[11px] text-[var(--text-muted)] px-1">
            <span>Policy: Socratic hints only. Direct answers are withheld to promote deep mastery.</span>
            <span class="font-mono">POST /dev/tutor/chat</span>
          </div>
        </div>
      </div>
    `;
  }

  renderChatMessage(msg) {
    if (msg.sender === 'student') {
      return `
        <div class="flex justify-end gap-3 animate-fade-in">
          <div class="max-w-xl bg-[var(--sage-primary-light)] border border-[var(--sage-badge)] rounded-2xl p-4 text-xs text-[var(--text-primary)] space-y-2">
            ${msg.attachment ? `
              <div class="p-2 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[11px] font-mono flex items-center gap-2 mb-2">
                <svg class="w-4 h-4 text-emerald-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/></svg>
                <span>Attached: ${msg.attachment}</span>
              </div>
            ` : ''}
            <p class="leading-relaxed">${msg.text}</p>
            <div class="text-[10px] text-[var(--text-muted)] text-right font-mono">${msg.time}</div>
          </div>
          <div class="w-8 h-8 rounded-full bg-[var(--sage-badge)] text-[var(--sage-badge-text)] flex items-center justify-center font-semibold text-xs shrink-0">
            AS
          </div>
        </div>
      `;
    }

    // Tutor Message Card (Matches Figma Screen 5 exactly!)
    return `
      <div class="flex items-start gap-3.5 animate-fade-in">
        <div class="w-8 h-8 rounded-xl bg-[var(--sage-primary)] text-white flex items-center justify-center shrink-0 shadow-sm mt-1">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
        </div>

        <div class="flex-1 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-5 shadow-sm space-y-4 text-xs">
          <div>
            <div class="flex items-center justify-between text-[10px] font-mono text-[var(--text-muted)] mb-1">
              <span>RESONANCE AI SOCRATIC TUTOR</span>
              <span>${msg.time || 'Just now'}</span>
            </div>
            <h4 class="font-serif text-lg font-normal text-[var(--text-primary)]">${msg.headline || "Let's explore this step-by-step."}</h4>
            <p class="text-[var(--text-secondary)] mt-1.5 leading-relaxed">${msg.body || msg.text || ""}</p>
          </div>

          <!-- Comparative Base Case Cards (Screen 5) -->
          ${msg.cards && msg.cards.length > 0 ? `
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
              ${msg.cards.map(c => `
                <div class="comparison-card space-y-2">
                  <h5 class="font-medium text-xs text-[var(--text-primary)]">${c.title}</h5>
                  <p class="text-[11px] text-[var(--text-secondary)] leading-normal">${c.subtitle}</p>
                  <pre class="p-2 rounded bg-[var(--bg-app)] text-[10px] font-mono text-[var(--text-primary)] border border-[var(--border-subtle)] overflow-x-auto">${c.code}</pre>
                </div>
              `).join('')}
            </div>
          ` : ''}

          <!-- Reflective Question Callout (Screen 5) -->
          ${msg.reflectiveQuestion ? `
            <div class="reflective-box space-y-1">
              <span class="text-[10px] font-mono font-semibold text-[var(--sage-primary)] uppercase tracking-wider">REFLECTIVE PROMPT</span>
              <p class="font-medium text-[var(--text-primary)] text-xs leading-relaxed">${msg.reflectiveQuestion}</p>
            </div>
          ` : ''}

          <!-- Step Progression Pill -->
          <div class="flex items-center justify-between pt-2 border-t border-[var(--border-subtle)] text-[11px]">
            <span class="font-mono text-[var(--text-muted)]">${msg.stepLabel || 'Step 1 of 3: Identify Return Type'}</span>
            <button class="btn-next-step text-[var(--sage-primary)] font-semibold hover:underline flex items-center gap-1">
              <span>Next Socratic Deduction</span>
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen 6: Stepped Progression & Light/Dark Theme Showcase
  // ========================================================================
  renderStepsScreen() {
    return `
      <div class="space-y-6 animate-fade-in">
        <div>
          <div class="text-[11px] font-mono uppercase tracking-wider text-[var(--text-muted)]">SOCRATIC METHODOLOGY</div>
          <h1 class="font-serif text-3xl font-normal text-[var(--text-primary)] tracking-tight mt-0.5">A clear next step at every moment.</h1>
          <p class="text-xs text-[var(--text-secondary)] mt-1">Stepped hint architecture scaffolded for independent cognitive mastery.</p>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <!-- Step 1 Card -->
          <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-sm space-y-4">
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 rounded bg-[var(--sage-primary-light)] text-[var(--sage-primary)] font-semibold">STAGE 01</span>
              <span class="text-xs text-[var(--text-muted)] font-mono">Status: Completed</span>
            </div>
            <div>
              <h3 class="font-serif text-xl font-normal text-[var(--text-primary)]">Deconstruct Problem Boundary</h3>
              <p class="text-xs text-[var(--text-secondary)] mt-1 leading-relaxed">Isolate the base case from the recursive propagation. Define the exact invariant your algorithm guarantees on an empty tree.</p>
            </div>
            <div class="p-3.5 rounded-xl bg-[var(--bg-app)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] font-mono">
              Base Invariant: Empty node return value must match the identity element of the operation (+ for sum, None for tree).
            </div>
          </div>

          <!-- Step 2 Card -->
          <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border-2 border-[var(--sage-primary)] shadow-md space-y-4 relative">
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 rounded bg-[var(--sage-primary)] text-white font-semibold">STAGE 02 • ACTIVE</span>
              <span class="text-xs text-emerald-600 font-mono font-medium">In Progress</span>
            </div>
            <div>
              <h3 class="font-serif text-xl font-normal text-[var(--text-primary)]">Recursive Step Deduction</h3>
              <p class="text-xs text-[var(--text-secondary)] mt-1 leading-relaxed">Formulate how the current root combines the results of the left subtree and right subtree.</p>
            </div>
            <div class="reflective-box text-xs space-y-1">
              <p class="font-medium text-[var(--text-primary)]">Write down the recurrence relation: T(n) = 2T(n/2) + O(1)</p>
            </div>
            <button id="btn-proceed-step-3" class="w-full py-2.5 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium transition-colors">
              Verify Deduction & Advance
            </button>
          </div>
        </div>

        <!-- Light & Dark Side-by-Side Preview from Screen 6 -->
        <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] space-y-4">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="font-serif text-lg font-normal text-[var(--text-primary)]">Dual Mode Atmosphere Preview</h3>
              <p class="text-xs text-[var(--text-secondary)]">Optimized for late night research and daylight lectures.</p>
            </div>
            <button id="btn-toggle-demo-theme" class="px-3.5 py-1.5 rounded-xl border border-[var(--border-subtle)] text-xs font-medium hover:bg-[var(--bg-surface-subtle)]">
              Toggle Current App Theme
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen 7: Transparency & Policy Matrix ("Know what help is available")
  // ========================================================================
  renderPolicyScreen() {
    return `
      <div class="space-y-6 animate-fade-in">
        <div>
          <div class="text-[11px] font-mono uppercase tracking-wider text-[var(--text-muted)]">POLICY TRANSPARENCY ENGINE</div>
          <h1 class="font-serif text-3xl font-normal text-[var(--text-primary)] tracking-tight mt-0.5">Know what help is available.</h1>
          <p class="text-xs text-[var(--text-secondary)] mt-1">Authoritative institutional guardrails ensure transparency and fair evaluation.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
          <!-- Column 1: Yellow (Process Disclosure) -->
          <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--policy-yellow-border)] flex flex-col justify-between shadow-sm space-y-5">
            <div class="space-y-4">
              <div class="badge-policy-yellow text-xs font-mono font-semibold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-amber-600"></span>
                YELLOW • PROCESS DISCLOSURE
              </div>
              <h3 class="font-serif text-xl font-normal text-[var(--text-primary)]">AI is allowed with required disclosure.</h3>
              <ul class="text-xs text-[var(--text-secondary)] space-y-2.5 leading-relaxed">
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-amber-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span>You may query concepts and receive stepped guidance.</span>
                </li>
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-amber-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span>You must disclose reflection notes upon code deposit.</span>
                </li>
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-amber-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span>Pseudonymized interaction logs are hash-chained to submission.</span>
                </li>
              </ul>
            </div>
            <button data-test-policy="demo-yellow" class="btn-test-policy w-full py-2.5 rounded-xl border border-[var(--policy-yellow-border)] bg-[var(--policy-yellow-bg)] text-[var(--policy-yellow-text)] hover:opacity-90 text-xs font-medium transition-opacity">
              Test Yellow Policy Gate
            </button>
          </div>

          <!-- Column 2: Red (Restricted / No AI) -->
          <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--policy-red-border)] flex flex-col justify-between shadow-sm space-y-5">
            <div class="space-y-4">
              <div class="badge-policy-red text-xs font-mono font-semibold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-red-600"></span>
                RED • RESTRICTED
              </div>
              <h3 class="font-serif text-xl font-normal text-[var(--text-primary)]">This assessment requires independent thought.</h3>
              <ul class="text-xs text-[var(--text-secondary)] space-y-2.5 leading-relaxed">
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-red-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  <span>Generative code assistance is strictly barred.</span>
                </li>
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-red-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  <span>Fail-closed policy: all tutor requests automatically refused.</span>
                </li>
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-red-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  <span>Oral viva defense required for authenticity verification.</span>
                </li>
              </ul>
            </div>
            <button data-test-policy="demo-red" class="btn-test-policy w-full py-2.5 rounded-xl border border-[var(--policy-red-border)] bg-[var(--policy-red-bg)] text-[var(--policy-red-text)] hover:opacity-90 text-xs font-medium transition-opacity">
              Test Red Fail-Closed Guard
            </button>
          </div>

          <!-- Column 3: Green (Full Integration) -->
          <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--policy-green-border)] flex flex-col justify-between shadow-sm space-y-5">
            <div class="space-y-4">
              <div class="badge-policy-green text-xs font-mono font-semibold px-2.5 py-1 rounded-full inline-flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-emerald-600"></span>
                GREEN • FULL INTEGRATION
              </div>
              <h3 class="font-serif text-xl font-normal text-[var(--text-primary)]">AI is integrated into your learning flow.</h3>
              <ul class="text-xs text-[var(--text-secondary)] space-y-2.5 leading-relaxed">
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span>Unlimited Socratic dialogue, error diagnosis, and review.</span>
                </li>
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span>Multimodal note OCR & sketch feedback via Gemini.</span>
                </li>
                <li class="flex items-start gap-2">
                  <svg class="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                  <span>Socratic bounds withhold direct solutions to preserve learning.</span>
                </li>
              </ul>
            </div>
            <button data-test-policy="demo-green" class="btn-test-policy w-full py-2.5 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium transition-colors shadow-sm">
              Test Green Socratic Tutoring
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen 3 & 12: Workspace & Profile Settings
  // ========================================================================
  renderSettingsScreen() {
    return `
      <div class="space-y-6 animate-fade-in max-w-4xl mx-auto">
        <div class="flex items-center justify-between">
          <div>
            <div class="text-[11px] font-mono uppercase tracking-wider text-[var(--text-muted)]">PREFERENCES & PRIVACY</div>
            <h1 class="font-serif text-3xl font-normal text-[var(--text-primary)] tracking-tight mt-0.5">A workspace that feels like you.</h1>
            <p class="text-xs text-[var(--text-secondary)] mt-1">Manage your student profile, learning preferences, and privacy controls.</p>
          </div>
          <button id="btn-save-settings" class="px-5 py-2.5 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium shadow-sm transition-colors">
            Save Preferences
          </button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
          <!-- Profile Card -->
          <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-sm space-y-4 md:col-span-2">
            <h3 class="font-serif text-lg font-normal text-[var(--text-primary)]">Student Profile</h3>
            <div class="flex items-center gap-4 pb-3 border-b border-[var(--border-subtle)]">
              <div class="w-14 h-14 rounded-full bg-[var(--sage-badge)] text-[var(--sage-badge-text)] flex items-center justify-center font-serif text-xl font-medium">
                AS
              </div>
              <div>
                <h4 class="font-medium text-sm text-[var(--text-primary)]">Ananya Sharma</h4>
                <p class="text-xs text-[var(--text-secondary)]">B.Tech Computer Science & Engineering • Year 3</p>
                <p class="text-[11px] font-mono text-[var(--text-muted)]">ID: 2024CSB1042 • IIT Delhi</p>
              </div>
            </div>

            <!-- Learning Style Selection -->
            <div class="space-y-3 pt-1">
              <h4 class="text-xs font-semibold text-[var(--text-primary)]">Tutoring & Socratic Guidance Style</h4>
              <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <button class="p-3 rounded-xl border border-[var(--sage-primary)] bg-[var(--sage-primary-light)] text-left space-y-1">
                  <span class="text-xs font-semibold text-[var(--sage-primary)] block">Stepped Discovery</span>
                  <span class="text-[10px] text-[var(--text-secondary)] leading-normal">Hints revealed progressively after student reflection.</span>
                </button>
                <button class="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] hover:border-[var(--border-strong)] text-left space-y-1">
                  <span class="text-xs font-semibold text-[var(--text-primary)] block">Concept Invariants</span>
                  <span class="text-[10px] text-[var(--text-secondary)] leading-normal">Focuses on invariants and edge case proofs.</span>
                </button>
                <button class="p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] hover:border-[var(--border-strong)] text-left space-y-1">
                  <span class="text-xs font-semibold text-[var(--text-primary)] block">Viva Rehearsal</span>
                  <span class="text-[10px] text-[var(--text-secondary)] leading-normal">Probing oral defense questions.</span>
                </button>
              </div>
            </div>

            <!-- Privacy & Compliance Controls -->
            <div class="space-y-3 pt-3 border-t border-[var(--border-subtle)]">
              <h4 class="text-xs font-semibold text-[var(--text-primary)]">Privacy & Statutory Residency (NFR-PRV-05)</h4>
              <div class="space-y-3">
                <label class="flex items-center justify-between cursor-pointer text-xs">
                  <div>
                    <span class="font-medium text-[var(--text-primary)] block">Process log pseudonyms</span>
                    <span class="text-[11px] text-[var(--text-muted)]">Application path never retains plaintext identity.</span>
                  </div>
                  <input type="checkbox" checked class="w-4 h-4 rounded text-[var(--sage-primary)]">
                </label>
                <label class="flex items-center justify-between cursor-pointer text-xs">
                  <div>
                    <span class="font-medium text-[var(--text-primary)] block">India data residency (ap-south-1)</span>
                    <span class="text-[11px] text-[var(--text-muted)]">Data strictly stored in Indian AWS/GCP regions.</span>
                  </div>
                  <input type="checkbox" checked disabled class="w-4 h-4 rounded text-[var(--sage-primary)] opacity-80">
                </label>
                <label class="flex items-center justify-between cursor-pointer text-xs">
                  <div>
                    <span class="font-medium text-[var(--text-primary)] block">Cryptographic audit trail verification</span>
                    <span class="text-[11px] text-[var(--text-muted)]">Append-only hash-chained receipts generated on deposit.</span>
                  </div>
                  <input type="checkbox" checked class="w-4 h-4 rounded text-[var(--sage-primary)]">
                </label>
              </div>
            </div>
          </div>

          <!-- Institution Details & Guarantee -->
          <div class="space-y-5">
            <div class="p-5 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] space-y-3 text-xs">
              <span class="text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)]">INSTITUTION AFFILIATION</span>
              <div>
                <h4 class="font-semibold text-sm text-[var(--text-primary)]">IIT Delhi</h4>
                <p class="text-xs text-[var(--text-secondary)]">Academic Year 2026-27 • Semester 5</p>
                <p class="text-[11px] font-mono text-[var(--text-muted)] mt-1">Tenant ID: 1697686969648</p>
              </div>
            </div>

            <div class="p-5 rounded-2xl bg-[var(--sage-primary-light)] border border-[var(--sage-badge)] space-y-2 text-xs">
              <div class="flex items-center gap-2 text-[var(--sage-primary)] font-semibold">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                <span>Zero Training Guarantee</span>
              </div>
              <p class="text-[11px] text-[var(--text-secondary)] leading-relaxed">
                Student code, reflections, and chat messages are never used to train public or proprietary foundation models. All data is governed under institutional RLS contracts.
              </p>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Viva Preparation Studio
  // ========================================================================
  renderVivaScreen() {
    return `
      <div class="space-y-6 animate-fade-in max-w-4xl mx-auto">
        <div>
          <div class="text-[11px] font-mono uppercase tracking-wider text-[var(--text-muted)]">ORAL DEFENSE SIMULATOR</div>
          <h1 class="font-serif text-3xl font-normal text-[var(--text-primary)] tracking-tight mt-0.5">Viva Voce Preparation</h1>
          <p class="text-xs text-[var(--text-secondary)] mt-1">Rehearse oral defense questions generated from your assignment interactions.</p>
        </div>

        <div class="p-6 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-sm space-y-5">
          <div class="p-4 rounded-xl bg-[var(--bg-app)] border border-[var(--border-subtle)] flex items-center justify-between">
            <div>
              <span class="text-[10px] font-mono uppercase tracking-wider text-[var(--sage-primary)] font-semibold">EXAMINER PERSPECTIVE</span>
              <h3 class="font-serif text-lg font-medium text-[var(--text-primary)] mt-0.5">Question 1: Recursion vs Iterative Invariants</h3>
            </div>
            <span class="text-xs font-mono px-2.5 py-1 rounded-full bg-[var(--sage-badge)] text-[var(--sage-badge-text)]">Complexity Defense</span>
          </div>

          <p class="text-xs text-[var(--text-secondary)] leading-relaxed">
            "In your binary tree recursion, what would happen to the call stack if given a degenerated tree of 100,000 nodes? How would you defend against RecursionError, and why would Morris Traversal or an explicit stack prevent stack overflow?"
          </p>

          <textarea rows="3" class="w-full text-xs p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-app)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--sage-primary)]" placeholder="Record your verbal reasoning or bulleted defense points here..."></textarea>

          <div class="flex justify-between items-center pt-2">
            <span class="text-[11px] text-[var(--text-muted)]">Layer 3C Viva Voce Engine • Anonymized Boundary</span>
            <button class="px-4 py-2 rounded-xl bg-[var(--sage-primary)] hover:bg-[var(--sage-primary-hover)] text-white text-xs font-medium">
              Submit Viva Response
            </button>
          </div>
        </div>
      </div>
    `;
  }

  // ========================================================================
  // Screen-Specific Event Listeners
  // ========================================================================
  attachScreenEvents(container) {
    // Welcome Form Sign In
    container.querySelector('#form-signin')?.addEventListener('submit', (e) => {
      e.preventDefault();
      this.showToast("Signed in successfully to IIT Delhi workspace.", "success");
      this.navigateTo('dashboard');
    });

    // Go to Signup
    container.querySelector('#btn-goto-signup')?.addEventListener('click', (e) => {
      e.preventDefault();
      this.navigateTo('signup');
    });

    // Go to Signin
    container.querySelector('#btn-goto-signin')?.addEventListener('click', (e) => {
      e.preventDefault();
      this.navigateTo('welcome');
    });

    // Signup Form Submit
    container.querySelector('#form-signup')?.addEventListener('submit', (e) => {
      e.preventDefault();
      this.showToast("Account created with verified institutional affiliation.", "success");
      this.navigateTo('dashboard');
    });

    // Dashboard quick buttons
    container.querySelector('#btn-dash-inquiry')?.addEventListener('click', () => {
      this.state.activeAssignmentId = 'demo-green';
      this.navigateTo('tutor');
    });

    container.querySelector('#btn-dash-viva')?.addEventListener('click', () => {
      this.navigateTo('viva');
    });

    container.querySelector('#btn-practice-viva-banner')?.addEventListener('click', () => {
      this.navigateTo('viva');
    });

    // Assignment Card Buttons
    container.querySelectorAll('.btn-open-workspace').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const aid = e.currentTarget.getAttribute('data-open-assignment');
        this.state.activeAssignmentId = aid;
        if (aid === 'demo-red') {
          this.navigateTo('policy');
        } else {
          this.navigateTo('tutor');
        }
      });
    });

    // Tutor Screen Events
    container.querySelector('#btn-back-dashboard')?.addEventListener('click', () => {
      this.navigateTo('dashboard');
    });

    container.querySelector('#btn-open-disclose-modal')?.addEventListener('click', () => {
      this.showModal('modal-disclose');
    });

    container.querySelector('#select-tutor-language')?.addEventListener('change', (e) => {
      this.state.selectedLanguage = e.target.value;
      this.showToast(`Response language set to ${e.target.value.toUpperCase()}`, "info");
    });

    // Tutor Send Message
    const inputMsg = container.querySelector('#input-tutor-message');
    const btnSend = container.querySelector('#btn-send-tutor');

    const handleSendMessage = async () => {
      const text = inputMsg?.value?.trim();
      if (!text && !this.state.attachedFile) return;

      const userText = text || "Examining attached diagram...";
      inputMsg.value = '';

      // Add student message to state
      this.state.chatMessages.push({
        id: `msg-${Date.now()}`,
        sender: 'student',
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        text: userText,
        attachment: this.state.attachedFile?.name || null,
      });

      this.state.isGenerating = true;
      this.render();

      try {
        const res = await this.api.sendTutorMessage({
          assignmentId: this.state.activeAssignmentId,
          message: userText,
          languagePreference: this.state.selectedLanguage,
          disclosureAcknowledged: this.state.disclosureAcknowledged,
        });

        this.state.isGenerating = false;

        if (res.ok && res.data) {
          const d = res.data;
          this.state.chatMessages.push({
            id: `msg-${Date.now()}`,
            sender: 'tutor',
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            headline: d.status === 'refused' ? "Action Barred by Policy Guard" : "Socratic Guidance",
            body: d.message,
            reflectiveQuestion: d.status !== 'refused' ? "What edge case should we check next before continuing?" : null,
            stepLabel: d.status !== 'refused' ? "Step 2 of 3: Deduce Invariant" : "Refusal Enforced",
            actionTaken: d.selected_action,
            intent: d.intent,
          });
        }
      } catch (err) {
        this.state.isGenerating = false;
        this.showToast("Error communicating with Layer 3 tutor", "error");
      }

      this.state.attachedFile = null;
      this.render();
      
      // Scroll chat to bottom
      const stream = document.getElementById('tutor-chat-stream');
      if (stream) stream.scrollTop = stream.scrollHeight;
    };

    btnSend?.addEventListener('click', handleSendMessage);
    inputMsg?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') handleSendMessage();
    });

    // Multimodal file upload
    const fileInput = container.querySelector('#file-multimodal');
    const btnTrigger = container.querySelector('#btn-trigger-upload');
    btnTrigger?.addEventListener('click', () => fileInput?.click());

    fileInput?.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (file) {
        this.state.attachedFile = file;
        const preview = container.querySelector('#attachment-preview');
        const nameEl = container.querySelector('#attachment-name');
        if (preview && nameEl) {
          nameEl.textContent = file.name;
          preview.classList.remove('hidden');
          preview.classList.add('flex');
        }
        this.showToast(`Attached: ${file.name} (Ready for OCR)`, "info");
      }
    });

    container.querySelector('#btn-remove-attachment')?.addEventListener('click', () => {
      this.state.attachedFile = null;
      const preview = container.querySelector('#attachment-preview');
      if (preview) {
        preview.classList.add('hidden');
        preview.classList.remove('flex');
      }
    });

    // Next Step Action inside tutor cards
    container.querySelectorAll('.btn-next-step').forEach(btn => {
      btn.addEventListener('click', () => {
        this.navigateTo('steps');
      });
    });

    // Policy Matrix Test Buttons
    container.querySelectorAll('.btn-test-policy').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const aid = e.currentTarget.getAttribute('data-test-policy');
        this.state.activeAssignmentId = aid;
        this.showToast(`Loaded policy for ${aid}`, "info");
        this.navigateTo('tutor');
      });
    });

    // Theme comparison button on Steps screen
    container.querySelector('#btn-toggle-demo-theme')?.addEventListener('click', () => {
      this.toggleTheme();
    });

    // Step 2 Proceed button
    container.querySelector('#btn-proceed-step-3')?.addEventListener('click', () => {
      this.showToast("Invariant verified! Stepped deduction approved.", "success");
      this.navigateTo('tutor');
    });

    // Save Settings
    container.querySelector('#btn-save-settings')?.addEventListener('click', () => {
      this.showToast("Settings and privacy controls saved successfully.", "success");
    });
  }

  // ========================================================================
  // Handle Disclosure Form Submission (Screen 8 -> Screen 9)
  // ========================================================================
  async handleDisclosureSubmit() {
    const reflection = document.getElementById('disclose-reflection-input')?.value || "Analyzed base case vs None return types.";
    
    this.hideModal('modal-disclose');
    this.showToast("Cryptographically signing submission...", "info");

    const receipt = await this.api.submitDisclosure({
      assignmentId: this.state.activeAssignmentId,
      reflectionNotes: reflection,
      checkedCriteria: ['concept', 'independent_code', 'audit_logs'],
      honorCodeAgreed: true,
    });

    this.state.disclosureAcknowledged = true;

    // Populate Receipt Modal
    const hashEl = document.getElementById('receipt-hash');
    const timeEl = document.getElementById('receipt-timestamp');
    if (hashEl) hashEl.textContent = receipt.verification_hash;
    if (timeEl) timeEl.textContent = receipt.timestamp;

    this.showModal('modal-receipt');
  }

  // ========================================================================
  // Render API Inspector Live Logs
  // ========================================================================
  renderInspectorLogs() {
    const container = document.getElementById('inspector-log-list');
    if (!container) return;

    if (this.api.logs.length === 0) {
      container.innerHTML = `<div class="text-[11px] text-[var(--text-muted)] italic">No network events yet. Pings will appear here.</div>`;
      return;
    }

    container.innerHTML = this.api.logs.map(log => `
      <div class="p-2.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-app)] space-y-1">
        <div class="flex items-center justify-between text-[11px]">
          <span class="font-bold ${log.status >= 200 && log.status < 300 ? 'text-emerald-600' : 'text-amber-600'}">
            ${log.method} ${log.endpoint}
          </span>
          <span class="text-[10px] text-[var(--text-muted)]">${log.durationMs}ms</span>
        </div>
        <div class="flex items-center justify-between text-[10px] text-[var(--text-muted)]">
          <span>Status: ${log.status || 'OK'}</span>
          <span>${log.timestamp.split('T')[1].slice(0, 8)}</span>
        </div>
      </div>
    `).join('');
  }
}

// Instantiate application on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  window.resonanceApp = new ResonanceApp();
});
