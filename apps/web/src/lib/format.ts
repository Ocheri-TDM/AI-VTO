const moneyFormatter = new Intl.NumberFormat("ru-RU", {
  maximumFractionDigits: 0,
});

export function formatPrice(value: number | null): string {
  return value === null
    ? "Цена не определена"
    : `${moneyFormatter.format(value)} ₸`;
}

const categories: Record<string, string> = {
  thermomug: "Термокружки",
  backpack: "Рюкзаки",
  notebook: "Ежедневники",
  pen: "Ручки",
  pencil: "Карандаши",
  thermos: "Термосы",
  marker: "Маркеры",
  highlighter: "Текстовыделители",
  glass: "Стаканы",
  plate: "Тарелки",
  tshirt: "Футболки",
  hoodie: "Худи",
  blanket: "Пледы",
  clock: "Часы",
  speaker: "Колонки",
  headphones: "Наушники",
  flashlight: "Фонарики",
  suitcase: "Чемоданы",
  wallet: "Кошельки",
  cardholder: "Визитницы",
  organizer: "Органайзеры",
  lanyard: "Ланъярды",
  mug: "Кружки",
  bottle: "Бутылки",
  umbrella: "Зонты",
  bag: "Сумки",
  powerbank: "Внешние аккумуляторы",
  charger: "Зарядные устройства",
  cable: "Кабели",
  lunchbox: "Ланчбоксы",
  accessory: "Аксессуары",
};

const colors: Record<string, { label: string; swatch: string }> = {
  NAVY: { label: "Тёмно-синий", swatch: "#263c63" },
  DARK_BLUE: { label: "Тёмно-синий", swatch: "#234780" },
  BLUE: { label: "Синий", swatch: "#416da9" },
  LIGHT_BLUE: { label: "Голубой", swatch: "#83b5d6" },
  ROYAL_BLUE: { label: "Королевский синий", swatch: "#365cca" },
  BLUE_GREY: { label: "Сине-серый", swatch: "#647e98" },
  BLACK: { label: "Чёрный", swatch: "#24282c" },
  WHITE: { label: "Белый", swatch: "#eeeeeb" },
  GREY: { label: "Серый", swatch: "#8a8b8d" },
  GREEN: { label: "Зелёный", swatch: "#48836a" },
  RED: { label: "Красный", swatch: "#b95255" },
  ORANGE: { label: "Оранжевый", swatch: "#dc925b" },
  YELLOW: { label: "Жёлтый", swatch: "#e6cf69" },
  PURPLE: { label: "Фиолетовый", swatch: "#9177bc" },
  PINK: { label: "Розовый", swatch: "#d999b5" },
  BROWN: { label: "Коричневый", swatch: "#8c6a52" },
  BEIGE: { label: "Бежевый", swatch: "#c8b396" },
};

export const categoryLabel = (value: string | null) =>
  value ? (categories[value.toLowerCase()] ?? "Мерч") : "Мерч";
export const colorLabel = (value: string) =>
  colors[value]?.label ?? "Другой оттенок";
export const colorSwatch = (value: string) =>
  colors[value]?.swatch ?? "#929b95";

export function safeWebUrl(
  value: string | null | undefined,
): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol)
      ? url.toString()
      : undefined;
  } catch {
    return undefined;
  }
}

export const isRunning = (status?: string) =>
  status === "queued" || status === "running";
