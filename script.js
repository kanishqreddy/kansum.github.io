/* STITCHED SEAM */
const path = document.getElementById("seamPath");
const length = path.getTotalLength();

path.style.strokeDasharray = length;
path.style.strokeDashoffset = length;

window.addEventListener("scroll", () => {
  const scroll = window.scrollY;
  const height = document.body.scrollHeight - window.innerHeight;
  const progress = scroll / height;
  path.style.strokeDashoffset = length * (1 - progress);
});

/* STITCH TEXT */
const texts = document.querySelectorAll(".stitch-text");

window.addEventListener("scroll", () => {
  const stitch = document.querySelector(".stitch");
  const start = stitch.offsetTop;
  const h = window.innerHeight;

  texts.forEach((t, i) => {
    t.classList.toggle(
      "active",
      window.scrollY > start + i * h &&
      window.scrollY < start + (i + 1) * h
    );
  });
});

/* LOOKBOOK SCROLL */
const lookbook = document.querySelector(".lookbook");
const track = document.querySelector(".track");

window.addEventListener("scroll", () => {
  const r = lookbook.getBoundingClientRect();
  if (r.top <= 0 && r.bottom >= window.innerHeight) {
    const p = Math.abs(r.top) / r.height;
    track.style.transform = `translateX(-${p * 60}vw)`;
  }
});
