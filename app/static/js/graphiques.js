/* Graphiques des tableaux de bord — SVG écrit à la main, aucune dépendance.
 *
 * Trois formes, trois métiers distincts :
 *   sparkline  — une tendance sur 30 jours, en pied de carte d'indicateur
 *   courbe     — une série à la fois, avec sélecteur, curseur et infobulle
 *   barres     — une répartition, chaque part portant son libellé
 *
 * ⚠️ Aucune palette catégorielle n'est fabriquée ici. La courbe n'affiche qu'une
 * série, donc elle porte l'accent de la marque ; les barres reçoivent leur teinte
 * de la donnée (couleur d'objectif, couleur de criticité), jamais de leur rang.
 *
 * ⚠️ Les graphiques sont dessinés à l'ouverture et au redimensionnement : mesurer
 * un conteneur replié donnerait une largeur nulle.
 */
(function () {
    'use strict';

    const NS = 'http://www.w3.org/2000/svg';
    const ACCENT = '#22d3ee';
    // En deçà, il n'y a pas de tendance : un point unique ne trace rien.
    const MINIMUM_POINTS = 2;

    const el = (nom, attrs) => {
        const n = document.createElementNS(NS, nom);
        for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
        return n;
    };

    const lire = (noeud, cle) => {
        try { return JSON.parse(noeud.dataset[cle] || '[]'); } catch (e) { return []; }
    };

    /* ── Sparkline ───────────────────────────────────────────────────────────
       Pas d'axe, pas de graduation, pas d'étiquette : la carte porte déjà la
       valeur. La courbe ne dit qu'une chose — la forme des 30 derniers jours. */
    function sparkline(hote) {
        const points = lire(hote, 'serie');
        const porteurs = points.filter(p => p.valeur > 0).length;
        hote.innerHTML = '';

        if (points.length < MINIMUM_POINTS || porteurs === 0) {
            // Une ligne plate à zéro se lirait comme une mesure. Un mot est plus juste.
            hote.innerHTML = '<span class="sv-spark-vide">aucun mouvement sur 30 jours</span>';
            return;
        }

        const L = hote.clientWidth || 180, H = 34, marge = 2;
        const max = Math.max(...points.map(p => p.valeur), 1);
        const x = i => (i / (points.length - 1)) * L;
        const y = v => H - marge - (v / max) * (H - marge * 2);

        const svg = el('svg', { width: '100%', height: H, viewBox: `0 0 ${L} ${H}`,
                                preserveAspectRatio: 'none', 'aria-hidden': 'true' });
        const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.valeur).toFixed(1)}`).join(' ');

        const aire = el('path', {
            d: `${d} L${L} ${H} L0 ${H} Z`,
            fill: 'var(--sv-teinte, ' + ACCENT + ')', opacity: '0.10',
        });
        const trait = el('path', {
            d, fill: 'none', stroke: 'var(--sv-teinte, ' + ACCENT + ')',
            'stroke-width': '2', 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
            opacity: '0.85', 'vector-effect': 'non-scaling-stroke',
        });
        svg.append(aire, trait);

        // Le dernier point est repéré : c'est celui que la valeur de la carte annonce.
        const dernier = points[points.length - 1];
        svg.appendChild(el('circle', {
            cx: x(points.length - 1), cy: y(dernier.valeur), r: 2.5,
            fill: 'var(--sv-teinte, ' + ACCENT + ')',
        }));

        const total = points.reduce((s, p) => s + p.valeur, 0);
        svg.appendChild(el('title', {})).textContent =
            `${total} sur 30 jours · maximum ${max} le ${points.find(p => p.valeur === max).jour}`;
        hote.appendChild(svg);
    }

    /* ── Courbe avec sélecteur ───────────────────────────────────────────────
       Une seule série est visible à la fois : c'est ce qui autorise une couleur
       unique. Superposer deux séries aurait exigé deux teintes distinguables,
       que le design system ne fournit pas pour cet usage. */
    function courbe(hote) {
        const jeux = JSON.parse(hote.dataset.series || '{}');
        const boutons = hote.parentElement.querySelectorAll('[data-serie]');
        const zone = hote.querySelector('.sv-graph-zone');
        const infobulle = hote.querySelector('.sv-graph-bulle');
        let courante = hote.dataset.defaut || Object.keys(jeux)[0];

        function tracer() {
            const points = jeux[courante] || [];
            zone.innerHTML = '';
            const porteurs = points.filter(p => p.valeur > 0).length;
            if (points.length < MINIMUM_POINTS || porteurs === 0) {
                zone.innerHTML = '<p class="sv-graph-vide">Aucun mouvement sur les 30 derniers jours.</p>';
                return;
            }

            const L = zone.clientWidth || 640, H = 210;
            const g = { h: 34, b: 26, g: 34, d: 12 };   // gouttières
            const l = L - g.g - g.d, h = H - g.h - g.b;
            const max = Math.max(...points.map(p => p.valeur), 1);
            // Échelle arrondie vers le haut : un axe qui s'arrête pile sur le maximum
            // fait toucher le plafond à la courbe.
            const plafond = max <= 4 ? max + 1 : Math.ceil(max * 1.15);
            const x = i => g.g + (i / (points.length - 1)) * l;
            const y = v => g.h + h - (v / plafond) * h;

            const svg = el('svg', { width: '100%', height: H, viewBox: `0 0 ${L} ${H}`,
                                    role: 'img', 'aria-label': hote.dataset.libelle || 'Évolution sur 30 jours' });

            // Grille : récessive par construction, elle situe sans se faire lire.
            const paliers = plafond <= 4 ? plafond : 4;
            for (let i = 0; i <= paliers; i++) {
                const v = Math.round((plafond / paliers) * i), yy = y(v);
                svg.appendChild(el('line', { x1: g.g, y1: yy, x2: L - g.d, y2: yy,
                                             stroke: 'rgba(148,163,184,0.13)', 'stroke-width': '1' }));
                const t = el('text', { x: g.g - 8, y: yy + 3.5, 'text-anchor': 'end',
                                       fill: 'var(--text-dim)', 'font-size': '10' });
                t.textContent = v;
                svg.appendChild(t);
            }

            const d = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.valeur).toFixed(1)}`).join(' ');
            svg.appendChild(el('path', { d: `${d} L${x(points.length - 1)} ${g.h + h} L${g.g} ${g.h + h} Z`,
                                         fill: ACCENT, opacity: '0.09' }));
            svg.appendChild(el('path', { d, fill: 'none', stroke: ACCENT, 'stroke-width': '2',
                                         'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));

            // Repères de date : trois seulement — début, milieu, fin. Trente
            // étiquettes se chevaucheraient et n'apprendraient rien de plus.
            [0, Math.floor(points.length / 2), points.length - 1].forEach(i => {
                const t = el('text', { x: x(i), y: H - 8, 'text-anchor': i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle',
                                       fill: 'var(--text-dim)', 'font-size': '10' });
                t.textContent = points[i].jour;
                svg.appendChild(t);
            });

            const curseur = el('line', { y1: g.h, y2: g.h + h, stroke: 'rgba(148,163,184,0.35)',
                                         'stroke-width': '1', opacity: '0' });
            const bille = el('circle', { r: 4, fill: ACCENT, stroke: 'var(--bg-card)',
                                         'stroke-width': '2', opacity: '0' });
            svg.append(curseur, bille);

            // Couche d'interaction : une cible large, plutôt qu'un survol du trait.
            const capte = el('rect', { x: g.g, y: g.h, width: l, height: h, fill: 'transparent' });
            svg.appendChild(capte);
            capte.addEventListener('pointermove', ev => {
                const boite = svg.getBoundingClientRect();
                const px = (ev.clientX - boite.left) * (L / boite.width);
                const i = Math.max(0, Math.min(points.length - 1,
                                   Math.round(((px - g.g) / l) * (points.length - 1))));
                const p = points[i];
                curseur.setAttribute('x1', x(i)); curseur.setAttribute('x2', x(i));
                curseur.setAttribute('opacity', '1');
                bille.setAttribute('cx', x(i)); bille.setAttribute('cy', y(p.valeur));
                bille.setAttribute('opacity', '1');
                infobulle.textContent = `${p.jour} — ${p.valeur} ${hote.dataset.unite || ''}`.trim();
                infobulle.style.opacity = '1';
                infobulle.style.left = Math.min(Math.max(x(i) / L * 100, 8), 92) + '%';
            });
            capte.addEventListener('pointerleave', () => {
                curseur.setAttribute('opacity', '0');
                bille.setAttribute('opacity', '0');
                infobulle.style.opacity = '0';
            });

            zone.appendChild(svg);
        }

        boutons.forEach(b => b.addEventListener('click', () => {
            boutons.forEach(o => o.classList.toggle('is-actif', o === b));
            courante = b.dataset.serie;
            hote.dataset.unite = b.dataset.unite || '';
            tracer();
        }));
        hote._tracer = tracer;
        tracer();
    }

    /* ── Barres de répartition ───────────────────────────────────────────────
       Forme retenue plutôt qu'un anneau : chaque part porte son libellé, donc
       l'identité ne repose jamais sur la seule couleur. Mesuré avec le
       validateur de palette — voir la note dans CLAUDE.md. */
    function barres(hote) {
        const parts = lire(hote, 'parts');
        hote.innerHTML = '';
        if (!parts.length) {
            hote.innerHTML = '<p class="sv-graph-vide">Rien à répartir pour le moment.</p>';
            return;
        }
        const max = Math.max(...parts.map(p => p.nombre), 1);
        parts.forEach(p => {
            const ligne = document.createElement('div');
            ligne.className = 'sv-part';
            ligne.innerHTML =
                `<span class="sv-part-tete">
                     <span class="sv-part-puce" style="background:${p.color}"></span>
                     <span class="sv-part-nom" title="${p.label}">${p.label}</span>
                     <span class="sv-part-chiffre">${p.nombre}<span class="sv-part-pct">${p.pct} %</span></span>
                 </span>
                 <span class="sv-part-piste">
                     <span class="sv-part-barre" style="width:${Math.max(p.nombre / max * 100, 3)}%; background:${p.color}"></span>
                 </span>`;
            hote.appendChild(ligne);
        });
    }

    function tout() {
        document.querySelectorAll('[data-graph="sparkline"]').forEach(sparkline);
        document.querySelectorAll('[data-graph="courbe"]').forEach(n => {
            if (n._tracer) n._tracer(); else courbe(n);
        });
        document.querySelectorAll('[data-graph="barres"]').forEach(barres);
    }

    document.addEventListener('DOMContentLoaded', tout);
    let minuteur;
    window.addEventListener('resize', () => {
        clearTimeout(minuteur);
        minuteur = setTimeout(tout, 150);
    });
})();
