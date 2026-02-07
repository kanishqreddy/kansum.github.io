const path = document.getElementById("seamPath");
const length = path.getTotalLength();

path.style.strokeDasharray = length;
path.style.strokeDashoffset = length;

document.addEventListener("mousemove", e => {
  const rect = path.getBoundingClientRect();
  const tension = Math.abs(e.clientX - rect.left - rect.width / 2);
  path.style.strokeWidth = Math.max(2, 6 - tension / 80);
});

window.addEventListener("scroll", () => {
  const p = window.scrollY /
    (document.body.scrollHeight - window.innerHeight);
  path.style.strokeDashoffset = length * (1 - p);
});
