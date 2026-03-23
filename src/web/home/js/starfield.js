/**
 * Polaris Starfield
 *
 * Renders a real starfield centered on Polaris (the North Star) using
 * azimuthal equidistant projection. Every star is a real cataloged star
 * with its true name, position, magnitude, and spectral color.
 *
 * Projection is scaled to the viewport diagonal so every monitor —
 * regardless of size or aspect ratio — sees the same star layout.
 */

(function () {
    "use strict";

    // ==========================================
    // Spectral Type Colors
    // ==========================================
    // RGB values based on real blackbody colors for each spectral class.

    const SPECTRAL_COLORS = {
        O: [155, 176, 255],
        B: [170, 191, 255],
        A: [215, 224, 255],
        F: [248, 243, 230],
        G: [255, 235, 180],
        K: [255, 190, 110],
        M: [255, 140, 100],
    };

    // ==========================================
    // Complete Star Catalog
    // ==========================================
    // All real stars with proper names only (no catalog numbers).
    // [name, RA (hours), Dec (degrees), apparent magnitude, spectral type]

    const STARS = [
        // ── Ursa Minor ──
        ["Polaris",      2.53,  89.26,  1.98, "F"],
        ["Kochab",      14.85,  74.16,  2.08, "K"],
        ["Pherkad",     15.35,  71.83,  3.00, "A"],
        ["Yildun",      17.54,  86.59,  4.36, "A"],
        ["Urodelus",    16.77,  82.04,  4.21, "G"],
        ["Ahfa al Farkadain", 16.29, 75.76, 4.25, "A"],
        ["Anwar al Farkadain", 15.74, 77.79, 4.95, "F"],

        // ── Ursa Major ──
        ["Dubhe",       11.06,  61.75,  1.79, "K"],
        ["Merak",       11.03,  56.38,  2.37, "A"],
        ["Phecda",      11.90,  53.69,  2.44, "A"],
        ["Megrez",      12.26,  57.03,  3.31, "A"],
        ["Alioth",      12.90,  55.96,  1.77, "A"],
        ["Mizar",       13.40,  54.93,  2.27, "A"],
        ["Alkaid",      13.79,  49.31,  1.86, "B"],
        ["Alcor",       13.42,  54.99,  3.99, "A"],
        ["Muscida",      8.50,  60.72,  3.36, "G"],
        ["Talitha",      8.99,  48.04,  3.14, "A"],
        ["Tania Borealis",10.37, 42.91,  3.45, "A"],
        ["Tania Australis",10.38,41.50,  3.06, "K"],
        ["Alula Borealis",11.31, 33.09,  3.49, "K"],
        ["Alula Australis",11.32,31.53,  3.79, "G"],
        ["Al Haud",      9.53,  51.68,  3.71, "K"],
        ["Alkaphrah",    9.06,  47.16,  3.69, "A"],
        ["Chalawan",     8.17,  65.02,  4.56, "G"],
        ["Taiyangshou",  9.52,  63.06,  3.67, "F"],

        // ── Cassiopeia ──
        ["Schedar",      0.68,  56.54,  2.24, "K"],
        ["Caph",         0.15,  59.15,  2.28, "F"],
        ["Navi",         0.95,  60.72,  2.47, "B"],
        ["Ruchbah",      1.91,  60.24,  2.68, "A"],
        ["Segin",        1.91,  63.67,  3.37, "B"],
        ["Achird",       0.82,  57.82,  3.44, "G"],
        ["Fulu",         0.49,  53.90,  4.17, "B"],
        ["Castula",      2.29,  67.41,  4.63, "A"],
        ["Marfak",       2.06,  72.42,  3.95, "A"],

        // ── Cepheus ──
        ["Alderamin",   21.31,  62.59,  2.51, "A"],
        ["Alfirk",      21.48,  70.56,  3.23, "B"],
        ["Errai",       23.66,  77.63,  3.21, "K"],
        ["Kurhah",      21.76,  73.36,  4.22, "A"],
        ["Al Kalb al Rai",23.07,75.39,  4.24, "K"],
        ["Erakis",      21.73,  58.78,  4.04, "M"],

        // ── Draco ──
        ["Eltanin",     17.94,  51.49,  2.23, "K"],
        ["Rastaban",    17.51,  52.30,  2.79, "G"],
        ["Thuban",      14.07,  64.38,  3.65, "A"],
        ["Edasich",     15.42,  58.97,  3.29, "K"],
        ["Grumium",     17.90,  56.87,  3.75, "K"],
        ["Aldhibah",    17.15,  65.71,  3.17, "A"],
        ["Taiyi",       18.35,  72.73,  3.29, "K"],
        ["Giausar",     11.52,  69.33,  3.85, "M"],
        ["Altais",      19.21,  67.66,  3.07, "B"],
        // Athebyne removed — duplicate of Grumium (both Xi Draconis)
        ["Dziban",      17.54,  72.15,  4.57, "F"],
        ["Kuma",        17.53,  55.17,  4.87, "F"],
        ["Alrakis",     17.51,  54.47,  4.68, "M"],
        ["Alsafi",      19.34,  69.66,  3.17, "K"],
        ["Tianyi",      12.93,  65.44,  4.66, "F"],
        ["Alathfar",    18.34,  71.34,  4.22, "K"],

        // ── Cygnus ──
        ["Deneb",       20.69,  45.28,  1.25, "A"],
        ["Sadr",        20.37,  40.26,  2.23, "F"],
        ["Albireo",     19.51,  27.96,  3.08, "K"],
        ["Fawaris",     19.75,  45.13,  2.87, "B"],
        ["Azelfafage",  21.74,  49.48,  4.56, "K"],
        ["Rukh",        20.84,  46.74,  3.79, "K"],
        ["Aljanah",     19.94,  35.08,  3.89, "K"],

        // ── Lyra ──
        ["Vega",        18.62,  38.78,  0.03, "A"],
        ["Sheliak",     18.83,  33.36,  3.52, "B"],
        ["Sulafat",     18.98,  32.69,  3.24, "B"],
        ["Aladfar",     18.99,  32.69,  4.36, "B"],
        ["Xihe",        18.91,  36.90,  4.30, "M"],

        // ── Perseus ──
        ["Mirfak",       3.41,  49.86,  1.79, "F"],
        ["Algol",        3.14,  40.96,  2.12, "B"],
        ["Menkib",       3.98,  35.79,  3.80, "O"],
        ["Atik",         3.74,  33.96,  2.85, "B"],
        ["Miram",        3.72,  47.79,  3.76, "G"],
        ["Misam",        3.15,  44.86,  4.04, "G"],
        // Delta Per removed — duplicate of Miram
        ["Gorgonea Quarta", 1.73, 50.69, 4.07, "B"],

        // ── Auriga ──
        ["Capella",      5.28,  46.00,  0.08, "G"],
        ["Menkalinan",   5.99,  44.95,  1.90, "A"],
        ["Mahasim",      5.03,  43.82,  3.17, "A"],
        ["Hassaleh",     4.95,  33.17,  2.69, "K"],
        ["Almaaz",       5.03,  43.82,  2.99, "F"],
        ["Saclateni",    5.11,  41.23,  3.72, "G"],
        ["Haedus",       5.11,  41.08,  3.18, "A"],

        // ── Gemini ──
        ["Castor",       7.58,  31.89,  1.58, "A"],
        ["Pollux",       7.76,  28.03,  1.14, "K"],
        ["Alhena",       6.63,  16.40,  1.93, "A"],
        ["Wasat",        7.34,  21.98,  3.53, "F"],
        ["Mebsuta",      6.73,  25.13,  3.06, "G"],
        ["Propus",       6.25,  22.51,  3.31, "M"],
        ["Tejat",        6.38,  22.51,  2.88, "M"],

        // ── Bootes ──
        ["Arcturus",    14.26,  19.18, -0.05, "K"],
        ["Nekkar",      15.03,  40.39,  3.50, "G"],
        ["Seginus",     14.53,  38.31,  3.03, "A"],
        ["Izar",        14.75,  27.07,  2.37, "K"],
        ["Muphrid",     13.91,  18.40,  2.68, "G"],
        ["Alkalurops",  15.08,  37.38,  4.31, "F"],

        // ── Corona Borealis ──
        ["Alphecca",    15.58,  26.71,  2.23, "A"],
        ["Nusakan",     15.46,  29.11,  3.68, "F"],

        // ── Hercules ──
        ["Rasalgethi",  17.24,  14.39,  3.35, "M"],
        ["Kornephoros", 16.50,  21.49,  2.77, "G"],
        ["Sarin",       16.69,  31.60,  3.14, "A"],
        ["Maasym",      17.58,  26.11,  4.40, "K"],

        // ── Camelopardalis ──
        ["Tonatiuh",     4.90,  66.34,  4.29, "O"],
        ["Nikawiy",      5.06,  60.44,  4.03, "G"],

        // ── Lynx ──
        ["Alsciaukat",   9.31,  34.39,  3.13, "K"],

        // ── Andromeda ──
        ["Alpheratz",    0.14,  29.09,  2.06, "B"],
        ["Mirach",       1.16,  35.62,  2.05, "M"],
        ["Almach",       2.06,  42.33,  2.17, "K"],
        ["Nembus",       1.63,  48.63,  3.57, "K"],

        // ── Triangulum ──
        ["Mothallah",    1.88,  29.58,  3.41, "F"],

        // ── Aries ──
        ["Hamal",        2.12,  23.46,  2.00, "K"],
        ["Sheratan",     1.91,  20.81,  2.64, "A"],

        // ── Leo Minor ──
        ["Praecipua",   10.89,  36.71,  3.83, "K"],

        // ── Canes Venatici ──
        ["Cor Caroli",  12.93,  38.32,  2.90, "A"],
        ["Chara",       12.56,  41.36,  4.24, "G"],
        ["La Superba",  12.76,  45.44,  5.42, "M"],

        // ── Lacerta ──

        // ── Vulpecula ──
        ["Anser",       19.48,  24.66,  4.44, "M"],

        // ── Sagitta ──
        ["Sham",        19.68,  18.01,  4.37, "G"],

        // ── Serpens Caput ──
        ["Unukalhai",   15.74,   6.43,  2.65, "K"],

        // ── Ophiuchus (north part) ──
        ["Rasalhague",  17.58,  12.56,  2.07, "A"],
        ["Cebalrai",    17.72,   4.57,  2.77, "K"],
        ["Sabik",       17.17,  -15.72, 2.43, "A"],

        // ── Corona Borealis extras ──
        ["Theta CrB",   15.55,  31.36,  4.14, "B"],

        // ── Pegasus (northern edge) ──
        ["Scheat",      23.06,  28.08,  2.42, "M"],
        ["Markab",      23.08,  15.21,  2.49, "B"],
        ["Matar",       22.72,  30.22,  2.94, "G"],
        ["Enif",        21.74,   9.88,  2.39, "K"],

        // ── Pisces (north) ──

        // ── Taurus (north) ──
        ["Elnath",       5.44,  28.61,  1.65, "B"],
        ["Aldebaran",    4.60,  16.51,  0.85, "K"],

        // ── Orion (north tip) ──
        ["Betelgeuse",   5.92,   7.41,  0.42, "M"],

        // ── Leo (north part) ──
        ["Regulus",     10.14,  11.97,  1.35, "B"],
        ["Denebola",    11.82,  14.57,  2.14, "A"],
        ["Algieba",     10.33,  19.84,  2.28, "K"],
        ["Zosma",       11.24,  20.52,  2.56, "A"],

        // ── Virgo (north edge) ──
        ["Vindemiatrix",13.04,  10.96,  2.83, "G"],


        // ── Aquila (north) ──
        ["Altair",      19.85,   8.87,  0.76, "A"],
        ["Tarazed",     19.77,  10.61,  2.72, "K"],
        ["Alshain",     19.92,   6.41,  3.71, "G"],

        // ── Delphinus ──
        ["Rotanev",     20.63,  14.60,  3.63, "F"],
        ["Sualocin",    20.66,  15.91,  3.77, "B"],

        // ── Equuleus ──
        ["Kitalpha",    21.26,   5.25,  3.92, "A"],

        // ── Serpens Cauda ──
        ["Alya",        18.94,   4.20,  4.62, "A"],

        // ── Coma Berenices ──
        ["Diadem",      13.17,  17.53,  4.32, "F"],
        ["Beta Com",    13.20,  27.88,  4.26, "G"],
        ["Gamma Com",   12.45,  28.27,  4.36, "K"],

        // ── Cancer ──
        ["Acubens",      8.97,  11.86,  4.25, "A"],
        ["Asellus Borealis", 8.72, 21.47, 4.66, "A"],
        ["Asellus Australis",8.75, 18.15, 3.94, "K"],
        ["Tegmine",      8.20,  17.65,  4.67, "F"],

        // ── Leo deeper ──
        ["Chertan",     11.24,  15.43,  3.33, "A"],
        ["Adhafera",    10.28,  23.42,  3.44, "F"],
        ["Ras Elased",  10.00,  23.77,  2.98, "K"],
        ["Subra",        9.69,   9.89,  3.52, "F"],

        // ── Ophiuchus deeper ──
        ["Han",         16.62,  -10.57, 2.54, "B"],
        ["Yed Prior",   16.24,  -3.69,  2.75, "M"],
        ["Yed Posterior",16.31, -4.69,  3.23, "K"],
        ["Marfik",      16.52,   1.98,  3.82, "A"],

        // ── Cygnus deeper ──
        ["Gienah Cyg",  20.77,  33.97,  2.48, "K"],

        // ── Aquarius / Pisces (outer edge) ──
        ["Sadalmelik",  22.10,  -0.32,  2.96, "G"],
        ["Fomalhaut",   22.96, -29.62,  1.16, "A"],

        // ── More outer ring stars for density ──
        ["Alphard",      9.46,  -8.66,  1.98, "K"],
        ["Procyon",      7.66,   5.22,  0.34, "F"],
        ["Sirius",       6.75, -16.72, -1.46, "A"],


        // ── Bootes deeper ──
        ["Princeps",    14.69,  29.75,  3.47, "F"],
        ["Xuange",      14.68,  38.31,  4.54, "K"],
        ["Rho Boo",     14.53,  30.37,  3.58, "K"],
        ["Taufon",      13.79,  17.46,  4.50, "F"],

        // ── Hercules deeper ──
        ["Pi Her",      17.25,  36.81,  3.16, "K"],
        ["Eta Her",     16.71,  38.92,  3.53, "G"],
        // Zeta Her removed — duplicate of Sarin

        // ── Libra (edge) ──
        ["Zubenelgenubi",14.85, -16.04,  2.75, "A"],
        ["Zubeneschamali",15.28, -9.38,  2.61, "B"],

        // ── Monoceros ──

        // ── Canis Minor ──
        ["Gomeisa",      7.45,   8.29,  2.90, "B"],

        // ── Additional named stars in sparse zones ──
        ["Ain",          4.48,  19.18,  3.53, "K"],
        ["Tabit",        4.83,   5.60,  3.19, "F"],
        ["Alzirr",       6.72,  12.90,  4.68, "G"],
    ];

    // ==========================================
    // Canvas Setup
    // ==========================================

    const canvas = document.getElementById("starfield");
    const ctx = canvas.getContext("2d");

    let width, height;
    let projectedStars = [];
    let mouseX = -1000, mouseY = -1000;

    const LABEL_RADIUS = 80;
    const TWINKLE_SPEED = 0.0008;

    // Deterministic phase per star (based on index)
    function starPhase(i) {
        // Simple hash for repeatable twinkle offset
        return ((i * 2654435761) & 0xFFFFFFFF) / 4294967296 * Math.PI * 2;
    }

    function resize() {
        const dpr = window.devicePixelRatio || 1;
        width = window.innerWidth;
        height = window.innerHeight;
        canvas.width = width * dpr;
        canvas.height = height * dpr;
        canvas.style.width = width + "px";
        canvas.style.height = height + "px";
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        projectStars();
    }

    // ==========================================
    // Projection — Azimuthal Equidistant from NCP
    // ==========================================
    // Uses the viewport diagonal so the star circle always covers
    // every corner, giving every monitor the same view.

    function projectStars() {
        const diagonal = Math.sqrt(width * width + height * height);
        const maxAngularDist = 90;
        const scale = (diagonal * 0.55) / maxAngularDist;

        const cx = width / 2;
        const cy = height / 2;

        projectedStars = STARS.map(([name, raH, dec, mag, spectral], i) => {
            const raRad = (raH / 24) * 2 * Math.PI;
            const angDist = 90 - dec;

            const r = angDist * scale;
            const x = cx + r * Math.sin(raRad);
            const y = cy - r * Math.cos(raRad);

            const radius = Math.max(0.6, 3.5 - mag * 0.5);
            const brightness = Math.max(0.25, 1 - mag * 0.12);
            const color = SPECTRAL_COLORS[spectral] || SPECTRAL_COLORS.A;

            return { x, y, radius, name, brightness, mag, color, phase: starPhase(i) };
        });

        // Pre-compute stable label positions with collision avoidance.
        // This runs once per resize so labels never jitter on mouse move.
        resolveLabels();
    }

    function resolveLabels() {
        const nameFont = "15px 'Century Gothic', Futura, 'Trebuchet MS', sans-serif";
        ctx.font = nameFont;

        for (const star of projectedStars) {
            const nameW = ctx.measureText(star.name).width;
            star.labelX = star.x + star.radius + 10;
            star.labelY = star.y - 4;
            star.labelW = nameW;
            star.labelH = 18;
        }

        // Sort by magnitude (brighter stars get placement priority)
        const sorted = [...projectedStars].sort((a, b) => a.mag - b.mag);
        const placed = [];

        for (const star of sorted) {
            // Skip off-screen stars
            if (star.x < -30 || star.x > width + 30 || star.y < -30 || star.y > height + 30) continue;

            let finalY = star.labelY;
            let attempts = 0;
            const maxAttempts = 6;

            while (attempts < maxAttempts) {
                let overlaps = false;
                for (const p of placed) {
                    if (
                        star.labelX < p.x + p.w + 6 &&
                        star.labelX + star.labelW + 6 > p.x &&
                        finalY - star.labelH / 2 < p.y + p.h / 2 &&
                        finalY + star.labelH / 2 > p.y - p.h / 2
                    ) {
                        overlaps = true;
                        break;
                    }
                }
                if (!overlaps) break;
                attempts++;
                const direction = attempts % 2 === 1 ? 1 : -1;
                const offset = Math.ceil(attempts / 2) * (star.labelH + 4);
                finalY = star.labelY + direction * offset;
            }

            star.resolvedLabelY = finalY;
            placed.push({ x: star.labelX, y: finalY, w: star.labelW, h: star.labelH });
        }
    }

    // ==========================================
    // Rendering
    // ==========================================

    function draw(time) {
        ctx.clearRect(0, 0, width, height);

        // Deep space gradient background
        const grad = ctx.createRadialGradient(
            width / 2, height / 2, 0,
            width / 2, height / 2, Math.max(width, height) * 0.7
        );
        grad.addColorStop(0, "#0a1020");
        grad.addColorStop(0.5, "#070d18");
        grad.addColorStop(1, "#03060c");
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, width, height);

        for (const star of projectedStars) {
            // Skip stars outside viewport
            if (star.x < -30 || star.x > width + 30 || star.y < -30 || star.y > height + 30) continue;

            const twinkle = 0.85 + 0.15 * Math.sin(time * TWINKLE_SPEED + star.phase);
            const alpha = star.brightness * twinkle;
            const [cr, cg, cb] = star.color;

            // Outer glow — only for brighter stars (performance)
            if (star.radius > 1.2) {
                const glowRadius = star.radius * 5;
                const glow = ctx.createRadialGradient(star.x, star.y, 0, star.x, star.y, glowRadius);
                glow.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.35})`);
                glow.addColorStop(0.4, `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.1})`);
                glow.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
                ctx.fillStyle = glow;
                ctx.beginPath();
                ctx.arc(star.x, star.y, glowRadius, 0, Math.PI * 2);
                ctx.fill();
            }

            // Core
            const coreR = Math.min(255, cr + 40);
            const coreG = Math.min(255, cg + 30);
            const coreB = Math.min(255, cb + 20);
            ctx.fillStyle = `rgba(${coreR}, ${coreG}, ${coreB}, ${alpha})`;
            ctx.beginPath();
            ctx.arc(star.x, star.y, star.radius * twinkle, 0, Math.PI * 2);
            ctx.fill();

            }

        // ── Label pass: use pre-computed positions, only control alpha ──
        const nameFont = "15px 'Century Gothic', Futura, 'Trebuchet MS', sans-serif";

        for (const star of projectedStars) {
            if (star.x < -30 || star.x > width + 30 || star.y < -30 || star.y > height + 30) continue;
            if (star.resolvedLabelY === undefined) continue;

            const dx = mouseX - star.x;
            const dy = mouseY - star.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist < LABEL_RADIUS) {
                const labelAlpha = Math.max(0, 1 - dist / LABEL_RADIUS);

                ctx.save();
                ctx.font = nameFont;
                ctx.textAlign = "left";
                ctx.textBaseline = "middle";
                ctx.fillStyle = `rgba(225, 235, 250, ${labelAlpha})`;
                ctx.fillText(star.name, star.labelX, star.resolvedLabelY);
                ctx.restore();
            }
        }

        requestAnimationFrame(draw);
    }

    // ==========================================
    // Events
    // ==========================================

    canvas.addEventListener("mousemove", (e) => {
        mouseX = e.clientX;
        mouseY = e.clientY;
    });

    canvas.addEventListener("mouseleave", () => {
        mouseX = -1000;
        mouseY = -1000;
    });

    window.addEventListener("resize", resize);

    // ==========================================
    // Init
    // ==========================================

    resize();
    requestAnimationFrame(draw);
})();
