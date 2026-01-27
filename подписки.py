import sys
import re
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from datetime import datetime
import subprocess
import json

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

# Установите зависимости:
# pip install PyQt5 selenium webdriver-manager beautifulsoup4 requests lxml

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
    from webdriver_manager.chrome import ChromeDriverManager
    from bs4 import BeautifulSoup
    import requests
    import lxml
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

@dataclass
class SubscriptionPlan:
    name: str
    price_per_month: float
    currency: str = "RUB"
    period: str = "month"
    features: List[str] = None
    
    def __post_init__(self):
        if self.features is None:
            self.features = []

@dataclass
class ServiceInfo:
    name: str
    category: str
    url: str
    plans: List[SubscriptionPlan]
    last_updated: datetime
    
    def to_dict(self):
        return {
            **asdict(self),
            'last_updated': self.last_updated.isoformat(),
            'plans': [asdict(p) for p in self.plans]
        }

class SmartSearcher:
    def __init__(self):
        self.driver = None
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        self.categories = {
            "Музыка": ["spotify", "yandex music", "apple music", "vk music", "deezer", "soundcloud"],
            "Видео": ["netflix", "youtube premium", "ivi", "okko", "start", "more.tv", "amediateka", "wink"],
            "Облако": ["yandex disk", "google drive", "dropbox", "mega", "icloud", "mail.ru облако"],
            "Игры": ["xbox game pass", "playstation plus", "steam", "nintendo switch online", "ea play"],
            "Образование": ["coursera", "udemy", "skillshare", "geekbrains", "stepik", "lingualeo"],
            "Книги": ["литрес", "bookmate", "mybook"],
            "Другие": []
        }
    
    def init_browser(self):
        if not SELENIUM_AVAILABLE:
            return False
        
        try:
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.driver.set_page_load_timeout(30)
            return True
        except Exception as e:
            print(f"Ошибка браузера: {e}")
            return False
    
    def search_service(self, service_name: str) -> ServiceInfo:
        try:
            category = self.detect_category(service_name)
            
            # Сначала попробуем API поиска
            plans, url = self.search_via_api(service_name)
            if plans:
                return ServiceInfo(
                    name=service_name,
                    category=category,
                    url=url,
                    plans=plans,
                    last_updated=datetime.now()
                )
            
            # Если API не сработал, попробуем Selenium
            if not self.driver:
                if not self.init_browser():
                    return self.create_fallback_service(service_name)
            
            # Пробуем разные поисковые запросы
            search_queries = [
                f"{service_name} подписка цена тарифы",
                f"{service_name} subscription price",
                f"{service_name} стоимость подписки",
                f"тарифы {service_name} 2024"
            ]
            
            for query in search_queries:
                try:
                    search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}&num=10"
                    self.driver.get(search_url)
                    time.sleep(2)
                    
                    # Ищем основные сайты в результатах поиска
                    page_source = self.driver.page_source
                    
                    # Парсим результаты Google
                    soup = BeautifulSoup(page_source, 'html.parser')
                    
                    # Ищем органические результаты
                    organic_results = soup.find_all('div', {'class': ['g', 'MjjYud']})
                    
                    for result in organic_results[:5]:
                        link_elem = result.find('a', href=True)
                        if link_elem:
                            url = link_elem['href']
                            if '/url?q=' in url:
                                url = re.search(r'/url\?q=(https?://[^&]+)', url).group(1)
                            
                            if not any(domain in url for domain in ['google.', 'facebook.', 'twitter.']):
                                try:
                                    page_plans = self.scrape_website(url)
                                    if page_plans:
                                        return ServiceInfo(
                                            name=service_name,
                                            category=category,
                                            url=url,
                                            plans=page_plans,
                                            last_updated=datetime.now()
                                        )
                                except Exception as e:
                                    print(f"Ошибка парсинга {url}: {e}")
                                    continue
                    
                except Exception as e:
                    print(f"Ошибка поиска по запросу '{query}': {e}")
                    continue
            
            # Если ничего не нашли, пробуем известные сайты напрямую
            return self.search_popular_service(service_name)
            
        except Exception as e:
            print(f"Ошибка поиска: {e}")
            return self.create_fallback_service(service_name)
    
    def search_via_api(self, service_name: str):
        """Попробуем найти информацию через различные API"""
        try:
            # Пробуем получить данные через DuckDuckGo или другие источники
            ddg_url = f"https://api.duckduckgo.com/?q={service_name}+subscription+price&format=json&pretty=1"
            response = self.session.get(ddg_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('Abstract'):
                    # Парсим текст на наличие цен
                    abstract = data['Abstract']
                    prices = re.findall(r'(\d+(?:\.\d+)?)\s*(?:руб|р\.?|₽|USD|\$)', abstract, re.IGNORECASE)
                    
                    if prices:
                        plans = []
                        for i, price in enumerate(prices[:3]):
                            try:
                                price_num = float(price)
                                plans.append(SubscriptionPlan(
                                    name=f"Тариф {i+1}",
                                    price_per_month=price_num,
                                    currency="RUB" if any(x in abstract.lower() for x in ['руб', 'р.', '₽']) else "USD"
                                ))
                            except:
                                pass
                        
                        if plans:
                            return plans, data.get('AbstractURL', '')
        except:
            pass
        
        return None, None
    
    def scrape_website(self, url: str):
        """Парсим конкретный веб-сайт"""
        try:
            # Пробуем через requests для скорости
            response = self.session.get(url, timeout=10)
            soup = BeautifulSoup(response.content, 'lxml')
            
            plans = []
            
            # Метод 1: Ищем цены с помощью различных селекторов
            price_selectors = [
                {'tag': 'span', 'class': ['price', 'cost', 'amount']},
                {'tag': 'div', 'class': ['pricing', 'tariff', 'plan-price']},
                {'tag': 'td', 'class': ['price']},
                {'tag': 'li', 'class': ['price']},
            ]
            
            all_text = soup.get_text()
            
            # Ищем цены в формате 499 ₽/мес, $9.99/month и т.д.
            price_patterns = [
                r'(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб\.?|₽|р\.?|RUB)\s*(?:в месяц|/мес|месяц|/мес\.)',
                r'(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:USD|\$|долл\.?)\s*(?:в месяц|/мес|month|/month)',
                r'цена[:\s\-]*(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб|₽|р|RUB)',
                r'стоимость[:\s\-]*(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб|₽|р|RUB)',
                r'(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб|₽|р|RUB).*?(?:месяц|мес)',
            ]
            
            for pattern in price_patterns:
                matches = re.finditer(pattern, all_text, re.IGNORECASE | re.DOTALL)
                for match in matches:
                    try:
                        # Извлекаем цену
                        price_str = match.group(1).replace(' ', '').replace(',', '.')
                        price = float(price_str)
                        
                        # Определяем валюту
                        currency = "RUB" if any(x in match.group(0).lower() for x in ['руб', '₽', 'р', 'rub']) else "USD"
                        
                        # Получаем контекст для определения названия тарифа
                        start = max(0, match.start() - 200)
                        end = min(len(all_text), match.end() + 200)
                        context = all_text[start:end]
                        
                        # Определяем название тарифа
                        name = "Тариф"
                        name_keywords = {
                            'базов': 'Базовый',
                            'стандарт': 'Стандарт',
                            'премиум': 'Премиум',
                            'pro': 'Pro',
                            'индивидуальн': 'Индивидуальный',
                            'семейн': 'Семейный',
                            'студенческ': 'Студенческий',
                            'эконом': 'Эконом',
                            'люкс': 'Люкс',
                            'бизнес': 'Бизнес'
                        }
                        
                        for keyword, tariff_name in name_keywords.items():
                            if keyword in context.lower():
                                name = tariff_name
                                break
                        
                        # Ищем описание
                        features = []
                        feature_keywords = [
                            'без рекламы', 'скачивание', 'офлайн', 'качество',
                            'устройств', 'профиль', '4k', 'hd', 'full hd',
                            'музыка', 'фильмы', 'сериалы', 'игры', 'облако'
                        ]
                        
                        for feature in feature_keywords:
                            if feature in context.lower():
                                features.append(feature.capitalize())
                        
                        # Ограничиваем количество фич
                        features = features[:5]
                        
                        # Проверяем, нет ли уже такого тарифа
                        if not any(abs(p.price_per_month - price) < 0.01 for p in plans):
                            plans.append(SubscriptionPlan(
                                name=name,
                                price_per_month=price,
                                currency=currency,
                                features=features if features else None
                            ))
                            
                    except Exception as e:
                        print(f"Ошибка обработки цены: {e}")
                        continue
            
            return plans[:5]  # Возвращаем максимум 5 тарифов
            
        except Exception as e:
            print(f"Ошибка парсинга сайта: {e}")
            return None
    
    def search_popular_service(self, service_name: str):
        """Прямой поиск для популярных сервисов"""
        service_name_lower = service_name.lower()
        
        # Сопоставление сервисов с их официальными страницами
        service_pages = {
            'netflix': 'https://www.netflix.com/ru/',
            'spotify': 'https://www.spotify.com/ru-ru/premium/',
            'yandex plus': 'https://plus.yandex.ru/',
            'яндекс плюс': 'https://plus.yandex.ru/',
            'youtube premium': 'https://www.youtube.com/premium',
            'ivi': 'https://www.ivi.ru/subscribe',
            'okko': 'https://okko.tv/',
            'start': 'https://start.ru/',
            'more.tv': 'https://more.tv/',
            'amediateka': 'https://www.amediateka.ru/',
            'litres': 'https://www.litres.ru/',
            'bookmate': 'https://ru.bookmate.com/',
            'mybook': 'https://mybook.ru/',
        }
        
        for key, url in service_pages.items():
            if key in service_name_lower:
                try:
                    plans = self.scrape_website(url)
                    if plans:
                        return ServiceInfo(
                            name=service_name,
                            category=self.detect_category(service_name),
                            url=url,
                            plans=plans,
                            last_updated=datetime.now()
                        )
                except:
                    pass
        
        return self.create_fallback_service(service_name)
    
    def detect_category(self, service_name: str) -> str:
        name_lower = service_name.lower()
        
        for category, keywords in self.categories.items():
            for keyword in keywords:
                if keyword in name_lower:
                    return category
        
        return "Другие"
    
    def get_fallback_plans(self, service_name: str) -> List[SubscriptionPlan]:
        name_lower = service_name.lower()
        
        # Расширенный список популярных сервисов
        service_plans = {
            'netflix': [
                SubscriptionPlan("С подлинками", 599, "RUB", features=["1 устройство", "Мобильное"]),
                SubscriptionPlan("Стандартный", 799, "RUB", features=["2 устройства", "HD"]),
                SubscriptionPlan("Премиум", 1299, "RUB", features=["4 устройства", "Ultra HD"])
            ],
            'spotify': [
                SubscriptionPlan("Индивидуальный", 269, "RUB", features=["Без рекламы", "Скачивание"]),
                SubscriptionPlan("Дуэт", 349, "RUB", features=["2 аккаунта"]),
                SubscriptionPlan("Семейный", 449, "RUB", features=["До 6 аккаунтов", "Родительский контроль"])
            ],
            'youtube premium': [
                SubscriptionPlan("Индивидуальный", 399, "RUB", features=["Без рекламы", "YouTube Music"]),
                SubscriptionPlan("Семейный", 699, "RUB", features=["До 6 человек", "Родительский контроль"])
            ],
            'яндекс плюс': [
                SubscriptionPlan("Яндекс Плюс", 249, "RUB", features=["Музыка", "Кино", "Кэшбэк"]),
                SubscriptionPlan("Мульти", 449, "RUB", features=["До 5 человек", "Детский режим"])
            ],
            'ivi': [
                SubscriptionPlan("Иви", 399, "RUB", features=["Фильмы и сериалы", "Мультики"]),
                SubscriptionPlan("Иви + Кинопоиск", 599, "RUB", features=["2 сервиса", "Эксклюзивы"])
            ],
            'okko': [
                SubscriptionPlan("Подписка", 399, "RUB", features=["Фильмы", "Сериалы", "Спорт"]),
                SubscriptionPlan("Подписка +", 799, "RUB", features=["Максимальное качество", "Одновременные просмотры"])
            ],
            'litres': [
                SubscriptionPlan("Подписка", 299, "RUB", features=["Аудиокниги", "Книги"]),
                SubscriptionPlan("Премиум", 599, "RUB", features=["Все книги", "Новинки"])
            ],
            'bookmate': [
                SubscriptionPlan("Базовый", 399, "RUB", features=["Книги", "Аудиокниги"]),
                SubscriptionPlan("Премиум", 799, "RUB", features=["Все книги", "Офлайн-чтение"])
            ],
        }
        
        # Пробуем найти точное совпадение
        for key, plans in service_plans.items():
            if key in name_lower:
                return plans
        
        # Пробуем частичное совпадение
        for key, plans in service_plans.items():
            key_words = key.split()
            if any(word in name_lower for word in key_words):
                return plans
        
        # Если не нашли, возвращаем общие планы
        return [
            SubscriptionPlan("Базовый", 199, "RUB", features=["Базовый доступ"]),
            SubscriptionPlan("Стандарт", 399, "RUB", features=["Полный доступ"]),
            SubscriptionPlan("Премиум", 799, "RUB", features=["Все возможности"])
        ]
    
    def create_fallback_service(self, service_name: str) -> ServiceInfo:
        category = self.detect_category(service_name)
        plans = self.get_fallback_plans(service_name)
        
        return ServiceInfo(
            name=service_name,
            category=category,
            url=f"https://www.google.com/search?q={service_name.replace(' ', '+')}+подписка",
            plans=plans,
            last_updated=datetime.now()
        )
    
    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass

# Остальной код остается без изменений (SearchThread, ServiceCard, MainWindow и т.д.)
# Классы SearchThread, ServiceCard, MainWindow, ComparisonDialog остаются как в исходном коде

class SearchThread(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name
    
    def run(self):
        try:
            self.progress.emit(f"Ищем {self.service_name}...")
            
            if not SELENIUM_AVAILABLE:
                raise Exception("Установите зависимости: pip install selenium webdriver-manager beautifulsoup4 requests lxml")
            
            searcher = SmartSearcher()
            service_info = searcher.search_service(self.service_name)
            searcher.close()
            
            self.finished.emit(service_info)
        except Exception as e:
            self.error.emit(str(e))

class ServiceCard(QFrame):
    def __init__(self, service_info):
        super().__init__()
        self.service_info = service_info
        self.initUI()
    
    def initUI(self):
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setMinimumWidth(350)
        self.setMaximumWidth(400)
        
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        title = QLabel(f"<h3>{self.service_info.name}</h3>")
        title.setStyleSheet("color: #2c3e50; font-weight: bold;")
        layout.addWidget(title)
        
        category = QLabel(f"📁 Категория: {self.service_info.category}")
        category.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(category)
        
        if self.service_info.url:
            url_label = QLabel(f"🔗 <a href='{self.service_info.url}'>Ссылка на сервис</a>")
            url_label.setOpenExternalLinks(True)
            layout.addWidget(url_label)
        
        update_label = QLabel(f"🕒 Обновлено: {self.service_info.last_updated.strftime('%d.%m.%Y %H:%M')}")
        update_label.setStyleSheet("color: #95a5a6; font-size: 10pt;")
        layout.addWidget(update_label)
        
        if self.service_info.plans:
            layout.addWidget(QLabel("<b>📊 Тарифные планы:</b>"))
            
            for plan in self.service_info.plans:
                plan_widget = self.create_plan_widget(plan)
                layout.addWidget(plan_widget)
        else:
            no_data = QLabel("❌ Тарифы не найдены")
            no_data.setStyleSheet("color: #e74c3c;")
            layout.addWidget(no_data)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def create_plan_widget(self, plan):
        widget = QFrame()
        widget.setFrameStyle(QFrame.Box)
        widget.setStyleSheet("""
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: 8px;
            border: 1px solid #dee2e6;
        """)
        
        layout = QVBoxLayout()
        
        # Название тарифа
        name_label = QLabel(f"<b>{plan.name}</b>")
        name_label.setStyleSheet("color: #2c3e50; font-size: 12pt;")
        layout.addWidget(name_label)
        
        # Цена
        price_text = f"💰 Цена: {plan.price_per_month} {plan.currency}/мес"
        if plan.currency == "RUB":
            price_text += f" (≈{plan.price_per_month * 0.011:.2f} $)"
        price_label = QLabel(price_text)
        price_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        layout.addWidget(price_label)
        
        # Особенности
        if plan.features:
            features_label = QLabel("📋 Включено:")
            features_label.setStyleSheet("color: #34495e;")
            layout.addWidget(features_label)
            
            for feature in plan.features:
                feature_item = QLabel(f"   • {feature}")
                feature_item.setStyleSheet("color: #7f8c8d;")
                layout.addWidget(feature_item)
        
        widget.setLayout(layout)
        return widget

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.services = []
        self.initUI()
    
    def initUI(self):
        self.setWindowTitle('Анализатор подписок v2.0')
        self.setGeometry(100, 100, 1400, 800)
        
        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Левая панель
        left_panel = self.create_left_panel()
        main_layout.addWidget(left_panel)
        
        # Правая панель (прокручиваемая)
        right_panel = self.create_right_panel()
        main_layout.addWidget(right_panel, 1)
        
        # Статус бар
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готово к поиску")
        
        # Стилизация
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f6fa;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
            }
            QLineEdit {
                padding: 8px;
                border: 2px solid #dfe6e9;
                border-radius: 4px;
            }
            QListWidget {
                background-color: white;
                border: 1px solid #dfe6e9;
                border-radius: 4px;
            }
        """)
    
    def create_left_panel(self):
        panel = QFrame()
        panel.setFixedWidth(350)
        panel.setStyleSheet("""
            QFrame {
                background-color: white;
                border-right: 1px solid #dfe6e9;
            }
        """)
        
        layout = QVBoxLayout(panel)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Заголовок
        title = QLabel("<h1 style='color: #2c3e50;'>🎯 Анализатор подписок</h1>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # Поле поиска
        layout.addWidget(QLabel("<b>🔍 Введите название сервиса:</b>"))
        self.service_input = QLineEdit()
        self.service_input.setPlaceholderText("Например: Netflix, Spotify, Яндекс Плюс...")
        layout.addWidget(self.service_input)
        
        # Кнопка поиска
        self.search_btn = QPushButton("🚀 Найти информацию")
        self.search_btn.clicked.connect(self.start_search)
        layout.addWidget(self.search_btn)
        
        # Прогресс
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #7f8c8d; font-style: italic;")
        layout.addWidget(self.progress_label)
        
        # Проверка зависимостей
        if not SELENIUM_AVAILABLE:
            warning = QLabel("⚠️ Установите зависимости!")
            warning.setStyleSheet("color: #e74c3c; font-weight: bold;")
            layout.addWidget(warning)
            install_btn = QPushButton("📦 Установить зависимости")
            install_btn.clicked.connect(self.install_deps)
            layout.addWidget(install_btn)
        
        # Разделитель
        layout.addWidget(QLabel("<hr>"))
        
        # Примеры
        layout.addWidget(QLabel("<b>💡 Примеры для поиска:</b>"))
        
        examples_grid = QGridLayout()
        examples = [
            ("Netflix", "#e74c3c"),
            ("Spotify", "#1DB954"),
            ("Яндекс Плюс", "#FFCC00"),
            ("Иви", "#00A2FF"),
            ("YouTube Premium", "#FF0000"),
            ("Окко", "#8E44AD"),
            ("Литрес", "#3498db"),
            ("Bookmate", "#2ecc71")
        ]
        
        row, col = 0, 0
        for example, color in examples:
            btn = QPushButton(example)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    padding: 6px;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    opacity: 0.9;
                }}
            """)
            btn.clicked.connect(lambda checked, ex=example: self.set_example(ex))
            examples_grid.addWidget(btn, row, col)
            col += 1
            if col > 1:
                col = 0
                row += 1
        
        layout.addLayout(examples_grid)
        
        # Разделитель
        layout.addWidget(QLabel("<hr>"))
        
        # Список найденных
        layout.addWidget(QLabel("<b>📋 Найденные сервисы:</b>"))
        
        self.services_list = QListWidget()
        self.services_list.itemDoubleClicked.connect(self.show_service_details)
        layout.addWidget(self.services_list)
        
        # Кнопки управления
        btn_layout = QHBoxLayout()
        
        self.compare_btn = QPushButton("📊 Сравнить")
        self.compare_btn.clicked.connect(self.show_comparison)
        self.compare_btn.setEnabled(False)
        self.compare_btn.setStyleSheet("background-color: #9b59b6;")
        btn_layout.addWidget(self.compare_btn)
        
        clear_btn = QPushButton("🗑️ Очистить")
        clear_btn.clicked.connect(self.clear_all)
        clear_btn.setStyleSheet("background-color: #e74c3c;")
        btn_layout.addWidget(clear_btn)
        
        layout.addLayout(btn_layout)
        
        # Статистика
        self.stats_label = QLabel("Найдено: 0 сервисов")
        self.stats_label.setStyleSheet("color: #7f8c8d; font-size: 10pt;")
        layout.addWidget(self.stats_label)
        
        layout.addStretch()
        return panel
    
    def install_deps(self):
        try:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", 
                                  "PyQt5", "selenium", "webdriver-manager", 
                                  "beautifulsoup4", "requests", "lxml"])
            QMessageBox.information(self, "Успех", "Зависимости установлены! Перезапустите программу.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось установить: {e}")
    
    def set_example(self, example):
        self.service_input.setText(example)
        self.start_search()
    
    def create_right_panel(self):
        panel = QScrollArea()
        panel.setWidgetResizable(True)
        panel.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)
        
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setSpacing(20)
        self.content_layout.setContentsMargins(20, 20, 20, 20)
        
        self.placeholder = QLabel("""
            <div style='text-align: center; padding: 50px;'>
                <h1 style='color: #7f8c8d;'>🎯 Анализатор подписок</h1>
                <p style='color: #95a5a6; font-size: 14pt;'>
                    Введите название сервиса для поиска информации о подписках<br>
                    Например: Netflix, Spotify, Яндекс Плюс и другие
                </p>
                <p style='color: #bdc3c7;'>
                    Программа автоматически найдет актуальные тарифы и цены
                </p>
            </div>
        """)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(self.placeholder)
        
        panel.setWidget(self.content_widget)
        return panel
    
    def start_search(self):
        service_name = self.service_input.text().strip()
        if not service_name:
            QMessageBox.warning(self, "Ошибка", "Введите название сервиса!")
            return
        
        self.search_btn.setEnabled(False)
        self.progress_label.setText(f"🔍 Ищем информацию о {service_name}...")
        
        self.thread = SearchThread(service_name)
        self.thread.finished.connect(self.on_search_finished)
        self.thread.error.connect(self.on_search_error)
        self.thread.progress.connect(self.progress_label.setText)
        self.thread.start()
    
    def on_search_finished(self, service_info):
        self.services.append(service_info)
        
        # Добавляем в список
        item_text = f"✅ {service_info.name}"
        if service_info.plans:
            min_price = min(p.price_per_month for p in service_info.plans)
            currency = service_info.plans[0].currency
            item_text += f" - от {min_price} {currency}"
        
        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, service_info)
        self.services_list.addItem(item)
        
        # Обновляем отображение
        self.update_display()
        self.compare_btn.setEnabled(len(self.services) >= 2)
        
        # Обновляем статистику
        self.stats_label.setText(f"Найдено: {len(self.services)} сервисов")
        
        self.search_btn.setEnabled(True)
        self.progress_label.setText("✅ Поиск завершен")
        self.status_bar.showMessage(f"Найден сервис: {service_info.name}")
        
        # Прокручиваем к новому элементу
        self.content_layout.update()
    
    def show_service_details(self, item):
        service_info = item.data(Qt.UserRole)
        if service_info:
            dialog = ServiceDetailsDialog(service_info, self)
            dialog.exec_()
    
    def on_search_error(self, error_msg):
        QMessageBox.critical(self, "Ошибка", f"Ошибка поиска: {error_msg}")
        self.search_btn.setEnabled(True)
        self.progress_label.setText("❌ Ошибка при поиске")
    
    def update_display(self):
        if self.placeholder:
            self.placeholder.setParent(None)
            self.placeholder = None
        
        # Очищаем старые карточки
        for i in reversed(range(self.content_layout.count())):
            widget = self.content_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        
        # Добавляем новые карточки
        for service in self.services:
            card = ServiceCard(service)
            self.content_layout.addWidget(card)
        
        # Добавляем растяжку в конце
        self.content_layout.addStretch()
    
    def show_comparison(self):
        if len(self.services) < 2:
            QMessageBox.warning(self, "Ошибка", "Для сравнения нужно минимум 2 сервиса")
            return
        
        dialog = ComparisonDialog(self.services, self)
        dialog.exec_()
    
    def clear_all(self):
        reply = QMessageBox.question(
            self, 'Подтверждение',
            'Очистить все найденные сервисы?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.services.clear()
            self.services_list.clear()
            self.update_display()
            self.compare_btn.setEnabled(False)
            self.stats_label.setText("Найдено: 0 сервисов")
            
            # Восстанавливаем placeholder
            self.placeholder = QLabel("""
                <div style='text-align: center; padding: 50px;'>
                    <h1 style='color: #7f8c8d;'>🎯 Анализатор подписок</h1>
                    <p style='color: #95a5a6; font-size: 14pt;'>
                        Введите название сервиса для поиска информации о подписках<br>
                        Например: Netflix, Spotify, Яндекс Плюс и другие
                    </p>
                    <p style='color: #bdc3c7;'>
                        Программа автоматически найдет актуальные тарифы и цены
                    </p>
                </div>
            """)
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.content_layout.addWidget(self.placeholder)
            
            self.status_bar.showMessage("Список очищен")

class ServiceDetailsDialog(QDialog):
    def __init__(self, service_info, parent=None):
        super().__init__(parent)
        self.service_info = service_info
        self.setWindowTitle(f"Детали: {service_info.name}")
        self.setGeometry(300, 300, 600, 500)
        
        layout = QVBoxLayout()
        
        # Информация о сервисе
        info_group = QGroupBox("Информация о сервисе")
        info_layout = QVBoxLayout()
        
        info_layout.addWidget(QLabel(f"<b>Название:</b> {service_info.name}"))
        info_layout.addWidget(QLabel(f"<b>Категория:</b> {service_info.category}"))
        info_layout.addWidget(QLabel(f"<b>URL:</b> <a href='{service_info.url}'>{service_info.url}</a>"))
        info_layout.addWidget(QLabel(f"<b>Обновлено:</b> {service_info.last_updated.strftime('%d.%m.%Y %H:%M')}"))
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # Тарифы
        if service_info.plans:
            plans_group = QGroupBox("Тарифные планы")
            plans_layout = QVBoxLayout()
            
            for plan in service_info.plans:
                plan_frame = QFrame()
                plan_frame.setFrameStyle(QFrame.Box)
                plan_frame.setStyleSheet("""
                    QFrame {
                        background-color: #f8f9fa;
                        padding: 10px;
                        margin: 5px;
                        border-radius: 5px;
                    }
                """)
                
                plan_layout = QVBoxLayout()
                plan_layout.addWidget(QLabel(f"<b>{plan.name}</b>"))
                plan_layout.addWidget(QLabel(f"💰 {plan.price_per_month} {plan.currency}/мес"))
                
                if plan.features:
                    features = QLabel("📋 " + ", ".join(plan.features))
                    features.setWordWrap(True)
                    plan_layout.addWidget(features)
                
                plan_frame.setLayout(plan_layout)
                plans_layout.addWidget(plan_frame)
            
            plans_group.setLayout(plans_layout)
            layout.addWidget(plans_group)
        else:
            layout.addWidget(QLabel("❌ Тарифы не найдены"))
        
        # Кнопка закрытия
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        
        self.setLayout(layout)

class ComparisonDialog(QDialog):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setWindowTitle("Сравнение тарифов")
        self.setGeometry(200, 200, 900, 600)
        
        layout = QVBoxLayout()
        
        # Таблица сравнения
        self.table = QTableWidget()
        self.table.setColumnCount(len(services) + 1)
        headers = ["Параметр"] + [s.name for s in services]
        self.table.setHorizontalHeaderLabels(headers)
        
        self.populate_table()
        self.table.resizeColumnsToContents()
        self.table.setAlternatingRowColors(True)
        
        layout.addWidget(self.table)
        
        # Итоговая информация
        total_label = QLabel(self.calculate_totals())
        total_label.setStyleSheet("font-weight: bold; color: #2c3c4d;")
        layout.addWidget(total_label)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        
        export_btn = QPushButton("📊 Экспорт в CSV")
        export_btn.clicked.connect(self.export_to_csv)
        btn_layout.addWidget(export_btn)
        
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
    
    def populate_table(self):
        # Собираем все уникальные параметры
        all_data = []
        
        # Базовые параметры
        all_data.append(("Категория", [s.category for s in self.services]))
        
        # Тарифы
        for i, service in enumerate(self.services):
            if service.plans:
                # Самый дешевый тариф
                cheapest = min(service.plans, key=lambda x: x.price_per_month)
                all_data.append((f"Тариф {i+1} (мин.)", 
                                [""] * i + [f"{cheapest.name}: {cheapest.price_per_month} {cheapest.currency}"] + [""] * (len(self.services) - i - 1)))
                
                # Самый дорогой тариф
                expensive = max(service.plans, key=lambda x: x.price_per_month)
                all_data.append((f"Тариф {i+1} (макс.)", 
                                [""] * i + [f"{expensive.name}: {expensive.price_per_month} {expensive.currency}"] + [""] * (len(self.services) - i - 1)))
                
                # Средняя цена
                avg_price = sum(p.price_per_month for p in service.plans) / len(service.plans)
                all_data.append((f"Средняя цена {i+1}", 
                                [""] * i + [f"{avg_price:.2f} {service.plans[0].currency}"] + [""] * (len(self.services) - i - 1)))
        
        # Устанавливаем строки
        self.table.setRowCount(len(all_data))
        
        for row, (param_name, values) in enumerate(all_data):
            self.table.setItem(row, 0, QTableWidgetItem(param_name))
            for col, value in enumerate(values):
                self.table.setItem(row, col + 1, QTableWidgetItem(str(value)))
    
    def calculate_totals(self):
        totals = []
        for service in self.services:
            if service.plans:
                min_price = min(p.price_per_month for p in service.plans)
                totals.append(f"{service.name}: от {min_price} {service.plans[0].currency}")
        
        return "Итог: " + " | ".join(totals)
    
    def export_to_csv(self):
        file_name, _ = QFileDialog.getSaveFileName(
            self, "Сохранить сравнение", "", "CSV Files (*.csv)"
        )
        
        if file_name:
            try:
                with open(file_name, 'w', encoding='utf-8') as f:
                    # Заголовки
                    headers = ["Параметр"] + [s.name for s in self.services]
                    f.write(";" + ";".join(headers[1:]) + "\n")
                    
                    # Данные
                    for row in range(self.table.rowCount()):
                        row_data = []
                        for col in range(self.table.columnCount()):
                            item = self.table.item(row, col)
                            row_data.append(item.text() if item else "")
                        f.write(";".join(row_data) + "\n")
                
                QMessageBox.information(self, "Успех", f"Данные сохранены в {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {e}")

def install_dependencies():
    try:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", 
                              "PyQt5", "selenium", "webdriver-manager", 
                              "beautifulsoup4", "requests", "lxml"])
        print("✅ Зависимости установлены")
        return True
    except:
        print("❌ Ошибка установки")
        return False

def main():
    if not SELENIUM_AVAILABLE:
        print("Устанавливаем зависимости...")
        if not install_dependencies():
            print("Установите вручную:")
            print("pip install PyQt5 selenium webdriver-manager beautifulsoup4 requests lxml")
            return
    
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()