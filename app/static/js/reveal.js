/* Reveals au défilement - partagé par les pages du site vitrine.
   IntersectionObserver uniquement, jamais d'écouteur de scroll : celui-ci se
   déclenche à chaque frame et fait tomber le défilement sur mobile.

   Le décalage entre enfants d'un même bloc est porté par la variable CSS --i,
   donc aucun délai n'est calculé ici. Sous prefers-reduced-motion, la feuille
   de style neutralise entrées et transitions : ce script se contente alors de
   marquer les blocs, sans rien animer.

   L'état masqué dépend de la classe `fx-js` posée par la page avant le rendu.
   Sans JavaScript, elle n'est jamais posée et la page reste lisible. */
document.addEventListener('DOMContentLoaded', () => {
    const blocs = document.querySelectorAll('.fx-reveal');
    if (!blocs.length) return;

    if (!('IntersectionObserver' in window)) {
        blocs.forEach(b => b.classList.add('is-visible'));
        return;
    }

    const obs = new IntersectionObserver((entrees) => {
        entrees.forEach(e => {
            if (!e.isIntersecting) return;
            e.target.classList.add('is-visible');
            obs.unobserve(e.target);          // une seule fois par bloc
        });
    }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });

    blocs.forEach(b => obs.observe(b));
});
