const texts = document.querySelectorAll(".sticky-text .text");
const visuals = document.querySelectorAll(".visuals .visual");

window.addEventListener("scroll", () => {
  const scrollPos = window.scrollY;
  const sectionTop = document.querySelector(".edition").offsetTop;
  const vh = window.innerHeight;

  texts.forEach((text, i) => {
    if (scrollPos >= sectionTop + i * vh * 0.6) {
      texts.forEach(t => t.classList.remove("active"));
      visuals.forEach(v => v.classList.remove("active"));

      text.classList.add("active");
      visuals[i].classList.add("active");
    }
  });
});
