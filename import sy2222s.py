import sys
import re
import time
import sqlite3
import hashlib
import secrets
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
import subprocess
import json
import imaplib
import email
from email.header import decode_header
import poplib
from collections import defaultdict
import csv
import os
import ssl

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
    payment_url: str = None
    
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
    popularity: int = 0
    
    def to_dict(self):
        return {
            **asdict(self),
            'last_updated': self.last_updated.isoformat(),
            'plans': [asdict(p) for p in self.plans]
        }

class UserManager:
    """Управление пользователями и их подписками"""
    
    def __init__(self):
        self.init_database()
        
    def init_database(self):
        """Инициализация базы данных SQLite"""
        self.conn = sqlite3.connect('subscriptions.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        
        # Таблица пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                username TEXT,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица подписок пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                plan_name TEXT,
                price REAL,
                currency TEXT,
                start_date DATE,
                renewal_date DATE,
                status TEXT DEFAULT 'active',
                payment_url TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Таблица избранных сервисов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS favorite_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                category TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        self.conn.commit()
    
    def hash_password(self, password: str, salt: str = None) -> Tuple[str, str]:
        """Хеширование пароля с солью"""
        if salt is None:
            salt = secrets.token_hex(16)
        
        password_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        ).hex()
        
        return password_hash, salt
    
    def register_user(self, email: str, username: str, password: str) -> bool:
        """Регистрация нового пользователя"""
        try:
            self.cursor.execute('SELECT id FROM users WHERE email = ?', (email,))
            if self.cursor.fetchone():
                return False
            
            password_hash, salt = self.hash_password(password)
            
            self.cursor.execute('''
                INSERT INTO users (email, username, password_hash, salt)
                VALUES (?, ?, ?, ?)
            ''', (email, username, password_hash, salt))
            
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка регистрации: {e}")
            return False
    
    def login_user(self, email: str, password: str) -> Optional[int]:
        """Аутентификация пользователя"""
        try:
            self.cursor.execute('''
                SELECT id, password_hash, salt FROM users WHERE email = ?
            ''', (email,))
            
            result = self.cursor.fetchone()
            if not result:
                return None
            
            user_id, stored_hash, salt = result
            input_hash, _ = self.hash_password(password, salt)
            
            if input_hash == stored_hash:
                return user_id
            else:
                return None
        except Exception as e:
            print(f"Ошибка входа: {e}")
            return None
    
    def save_subscription(self, user_id: int, service_info: ServiceInfo, plan: SubscriptionPlan):
        """Сохранение подписки пользователя"""
        try:
            self.cursor.execute('''
                INSERT OR REPLACE INTO user_subscriptions 
                (user_id, service_name, plan_name, price, currency, start_date, renewal_date, payment_url)
                VALUES (?, ?, ?, ?, ?, DATE('now'), DATE('now', '+1 month'), ?)
            ''', (
                user_id,
                service_info.name,
                plan.name,
                plan.price_per_month,
                plan.currency,
                plan.payment_url
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка сохранения подписки: {e}")
            return False
    
    def get_user_subscriptions(self, user_id: int) -> List[Dict]:
        """Получение всех подписок пользователя"""
        try:
            self.cursor.execute('''
                SELECT 
                    service_name,
                    plan_name,
                    price,
                    currency,
                    start_date,
                    renewal_date,
                    status,
                    payment_url
                FROM user_subscriptions 
                WHERE user_id = ? AND status = 'active'
                ORDER BY renewal_date
            ''', (user_id,))
            
            columns = [desc[0] for desc in self.cursor.description]
            subscriptions = []
            
            for row in self.cursor.fetchall():
                sub_dict = dict(zip(columns, row))
                subscriptions.append(sub_dict)
            
            return subscriptions
        except Exception as e:
            print(f"Ошибка получения подписок: {e}")
            return []
    
    def add_favorite_service(self, user_id: int, service_name: str, category: str):
        """Добавление сервиса в избранное"""
        try:
            self.cursor.execute('''
                INSERT OR IGNORE INTO favorite_services (user_id, service_name, category)
                VALUES (?, ?, ?)
            ''', (user_id, service_name, category))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка добавления в избранное: {e}")
            return False
    
    def get_favorite_services(self, user_id: int) -> List[Dict]:
        """Получение избранных сервисов пользователя"""
        try:
            self.cursor.execute('''
                SELECT service_name, category, added_at 
                FROM favorite_services 
                WHERE user_id = ? 
                ORDER BY added_at DESC
            ''', (user_id,))
            
            favorites = []
            for row in self.cursor.fetchall():
                favorites.append({
                    'service_name': row[0],
                    'category': row[1],
                    'added_at': row[2]
                })
            
            return favorites
        except Exception as e:
            print(f"Ошибка получения избранного: {e}")
            return []
    
    def delete_subscription(self, user_id: int, service_name: str) -> bool:
        """Удаление подписки пользователя"""
        try:
            self.cursor.execute('''
                DELETE FROM user_subscriptions 
                WHERE user_id = ? AND service_name = ?
            ''', (user_id, service_name))
            self.conn.commit()
            return self.cursor.rowcount > 0
        except Exception as e:
            print(f"Ошибка удаления подписки: {e}")
            return False
    
    def clear_favorites(self, user_id: int) -> bool:
        """Очистка избранного пользователя"""
        try:
            self.cursor.execute('DELETE FROM favorite_services WHERE user_id = ?', (user_id,))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка очистки избранного: {e}")
            return False

class EmailScanner:
    """Сканирование почты для поиска подписок"""
    
    def __init__(self):
        self.imap_servers = {
            'gmail.com': ('imap.gmail.com', 993),
            'yandex.ru': ('imap.yandex.ru', 993),
            'mail.ru': ('imap.mail.ru', 993),
            'outlook.com': ('outlook.office365.com', 993),
            'icloud.com': ('imap.mail.me.com', 993),
            'rambler.ru': ('imap.rambler.ru', 993),
            'bk.ru': ('imap.mail.ru', 993),
            'list.ru': ('imap.mail.ru', 993),
            'inbox.ru': ('imap.mail.ru', 993)
        }
        
        self.subscription_keywords = [
            'подписка', 'subscription', 'продление', 'renewal',
            'ежемесячный', 'monthly', 'тариф', 'tariff',
            'оплата', 'payment', 'счет', 'invoice',
            'автопродление', 'auto-renew', 'премиум', 'premium',
            'аккаунт', 'account', 'сервис', 'service',
            'биллинг', 'billing', 'платеж', 'charge',
            'списание', 'withdrawal', 'возобновление', 'renew',
            'membership', 'членство', 'абонемент', 'subscription'
        ]
        
        self.patterns = {
            'service_name': [
                r'от\s+([А-Яа-яA-Za-z\s\.]+)\s*(?:подписка|subscription)',
                r'([А-Яа-яA-Za-z\s\.]+)\s*-\s*оплата',
                r'сервис[:\s]+([А-Яа-яA-Za-z\s\.]+)',
                r'Счет от ([А-Яа-яA-Za-z\s\.]+)',
                r'([А-Яа-яA-Za-z\s\.]+)\s*(?:ежемесячная оплата|monthly payment)'
            ],
            'price': [
                r'(\d+(?:[,\s]\d+)*(?:\.\d+)?)\s*(?:руб|р\.|RUB|₽|USD|\$)',
                r'сумма[:\s]*(\d+(?:[,\s]\d+)*(?:\.\d+)?)',
                r'списано[:\s]*(\d+(?:[,\s]\d+)*(?:\.\d+)?)',
                r'оплата[:\s]*(\d+(?:[,\s]\d+)*(?:\.\d+)?)',
                r'charged[:\s]*(\d+(?:[,\s]\d+)*(?:\.\d+)?)',
                r'Amount[:\s]*(\d+(?:[,\s]\d+)*(?:\.\d+)?)'
            ],
            'period': [
                r'за\s*(месяц|month|ежемесячно)',
                r'период[:\s]*(\d+)\s*(?:мес|month)',
                r'ежемесячная',
                r'monthly'
            ]
        }
    
    def extract_domain(self, email_address: str) -> str:
        return email_address.split('@')[-1].lower()
    
    def connect_to_email(self, email_address: str, password: str, imap_server: str = None, port: int = None):
        try:
            if not imap_server:
                domain = self.extract_domain(email_address)
                if domain in self.imap_servers:
                    imap_server, port = self.imap_servers[domain]
                else:
                    imap_server = f"imap.{domain}"
                    port = 993
            
            print(f"Подключаемся к {imap_server}:{port}...")
            
            context = ssl.create_default_context()
            
            mail = imaplib.IMAP4_SSL(imap_server, port, ssl_context=context)
            
            mail.login(email_address, password)
            mail.select('inbox')
            
            print("Успешно подключились к почте")
            return mail
        except imaplib.IMAP4.error as e:
            error_msg = str(e)
            print(f"Ошибка IMAP: {error_msg}")
            
            if any(keyword in error_msg.lower() for keyword in ['invalid credentials', 'authentication failed', 
                                                                'login failed', 'неверный пароль', 'неправильный пароль',
                                                                'неправильные учетные данные', 'ошибка аутентификации']):
                raise Exception("Неправильный пароль от почты. Пожалуйста, проверьте правильность ввода пароля.")
            else:
                raise Exception(f"Ошибка подключения к почте: {error_msg}")
        except ssl.SSLError as e:
            raise Exception(f"Ошибка SSL при подключении к почте: {e}")
        except Exception as e:
            error_msg = str(e)
            print(f"Общая ошибка подключения: {error_msg}")
            
            auth_keywords = [
                'password', 'парол', 'credentials', 'учетные данные',
                'auth', 'аутентификац', 'login', 'вход'
            ]
            
            if any(keyword in error_msg.lower() for keyword in auth_keywords):
                raise Exception("Неправильный пароль от почты или ошибка аутентификации. Проверьте правильность ввода пароля.")
            else:
                raise Exception(f"Ошибка подключения к почтовому серверу: {error_msg}")
    
    def scan_for_subscriptions(self, email_address: str, password: str, days_back: int = 90):
        """Сканирование почты на наличие писем о подписках"""
        subscriptions = []
        processed_emails = 0
        last_messages = []
        
        try:
            mail = self.connect_to_email(email_address, password)
            if not mail:
                return subscriptions, last_messages
            
            date_since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
            
            result, data = mail.search(None, f'(SINCE "{date_since}")')
            
            if result != 'OK':
                return subscriptions, last_messages
            
            email_ids = data[0].split()
            email_ids = email_ids[-100:]  # Последние 100 писем
            
            total_emails = len(email_ids)
            
            for i, email_id in enumerate(email_ids):
                try:
                    result, data = mail.fetch(email_id, '(RFC822)')
                    if result != 'OK':
                        continue
                    
                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    subject = self.decode_header(msg['Subject'])
                    from_address = msg['From']
                    date_str = msg.get('Date', '')
                    
                    processed_emails += 1
                    
                    message_info = {
                        'subject': subject[:100] + '...' if len(subject) > 100 else subject,
                        'from': from_address[:50] + '...' if len(from_address) > 50 else from_address,
                        'date': date_str,
                        'processed': True,
                        'is_subscription': False
                    }
                    
                    if self.is_subscription_email(subject, from_address):
                        subscription_info = self.extract_subscription_info(msg)
                        if subscription_info:
                            subscription_info['found_in_subject'] = subject
                            subscription_info['found_in_from'] = from_address
                            subscriptions.append(subscription_info)
                            message_info['is_subscription'] = True
                            message_info['service'] = subscription_info.get('service_name', 'Неизвестно')
                    
                    last_messages.append(message_info)
                    
                    if len(last_messages) > 20:
                        last_messages = last_messages[-20:]
                        
                except Exception as e:
                    print(f"Ошибка обработки письма: {e}")
                    continue
            
            mail.logout()
            
        except Exception as e:
            print(f"Ошибка сканирования почты: {e}")
            raise e
        
        return subscriptions, last_messages
    
    def decode_header(self, header):
        if header is None:
            return ""
        
        decoded_parts = []
        for part, encoding in decode_header(header):
            if isinstance(part, bytes):
                if encoding:
                    try:
                        decoded_parts.append(part.decode(encoding))
                    except:
                        decoded_parts.append(part.decode('utf-8', errors='ignore'))
                else:
                    decoded_parts.append(part.decode('utf-8', errors='ignore'))
            else:
                decoded_parts.append(part)
        
        return ''.join(decoded_parts)
    
    def is_subscription_email(self, subject: str, from_address: str) -> bool:
        subject_lower = subject.lower()
        from_lower = from_address.lower()
        
        for keyword in self.subscription_keywords:
            if keyword in subject_lower:
                return True
        
        service_domains = [
            'netflix.com', 'spotify.com', 'yandex.ru', 'google.com',
            'steampowered.com', 'playstation.com', 'microsoft.com',
            'apple.com', 'amazon.com', 'ivi.ru', 'okko.tv', 'start.ru',
            'youtube.com', 'twitch.tv', 'discord.com', 'telegram.org',
            'patreon.com', 'boosty.to', 'github.com', 'dropbox.com',
            'icloud.com', 'mail.ru', 'rambler.ru', 'vk.com',
            'more.tv', 'amediateka.ru', 'wink.ru', 'megogo.net',
            'litres.ru', 'bookmate.com', 'mybook.ru', 'storytel.com',
            'coursera.org', 'udemy.com', 'skillshare.com', 'geekbrains.ru',
            'stepik.org', 'lingualeo.com', 'skyeng.ru'
        ]
        
        for domain in service_domains:
            if domain in from_lower:
                return True
        
        return False
    
    def extract_subscription_info(self, msg) -> Optional[Dict]:
        try:
            text_content = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        try:
                            text_content += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            pass
            else:
                text_content = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            
            text_content_lower = text_content.lower()
            
            service_name = None
            for pattern in self.patterns['service_name']:
                match = re.search(pattern, text_content, re.IGNORECASE)
                if match:
                    service_name = match.group(1).strip()
                    break
            
            if not service_name:
                from_header = msg['From']
                match = re.search(r'<?([^<>\s@]+@([^<>\s@]+))>?', from_header)
                if match:
                    domain = match.group(2)
                    domain_to_service = {
                        'netflix.com': 'Netflix',
                        'spotify.com': 'Spotify',
                        'yandex.ru': 'Яндекс',
                        'google.com': 'Google',
                        'steampowered.com': 'Steam',
                        'playstation.com': 'PlayStation',
                        'microsoft.com': 'Microsoft',
                        'apple.com': 'Apple',
                        'amazon.com': 'Amazon',
                        'ivi.ru': 'Иви',
                        'okko.tv': 'Окко',
                        'start.ru': 'Start',
                        'youtube.com': 'YouTube',
                        'twitch.tv': 'Twitch',
                        'discord.com': 'Discord',
                        'telegram.org': 'Telegram',
                        'patreon.com': 'Patreon',
                        'boosty.to': 'Boosty',
                        'github.com': 'GitHub',
                        'dropbox.com': 'Dropbox',
                        'icloud.com': 'iCloud',
                        'mail.ru': 'Mail.ru',
                        'rambler.ru': 'Rambler',
                        'vk.com': 'VK',
                        'more.tv': 'More.TV',
                        'amediateka.ru': 'Amediateka',
                        'wink.ru': 'Wink',
                        'megogo.net': 'MEGOGO',
                        'litres.ru': 'Литрес',
                        'bookmate.com': 'Bookmate',
                        'mybook.ru': 'MyBook',
                        'storytel.com': 'Storytel',
                        'coursera.org': 'Coursera',
                        'udemy.com': 'Udemy',
                        'skillshare.com': 'Skillshare',
                        'geekbrains.ru': 'GeekBrains',
                        'stepik.org': 'Stepik',
                        'lingualeo.com': 'Lingualeo',
                        'skyeng.ru': 'Skyeng'
                    }
                    service_name = domain_to_service.get(domain, domain.split('.')[0].capitalize())
            
            price = None
            currency = "RUB"
            for pattern in self.patterns['price']:
                match = re.search(pattern, text_content, re.IGNORECASE)
                if match:
                    price_str = match.group(1).replace(' ', '').replace(',', '.')
                    try:
                        price = float(price_str)
                        if any(x in match.group(0).lower() for x in ['usd', '$']):
                            currency = "USD"
                        break
                    except:
                        pass
            
            period = "month"
            for pattern in self.patterns['period']:
                match = re.search(pattern, text_content_lower)
                if match:
                    period = match.group(1)
                    break
            
            if service_name:
                return {
                    'service_name': service_name,
                    'price': price,
                    'currency': currency,
                    'period': period,
                    'found_date': datetime.now().strftime('%Y-%m-%d'),
                    'email_content_preview': text_content[:200] + '...' if len(text_content) > 200 else text_content
                }
        
        except Exception as e:
            print(f"Ошибка извлечения информации: {e}")
        
        return None

class SmartSearcher:
    def __init__(self):
        self.driver = None
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        self.categories = {
            "Музыка": {
                "keywords": ["spotify", "yandex music", "apple music", "vk music", "deezer", "soundcloud", "яндекс музыка"],
                "popular_services": [
                    ("Spotify", 95),
                    ("Яндекс Музыка", 85),
                    ("Apple Music", 80),
                    ("VK Музыка", 75),
                    ("Deezer", 60),
                    ("SoundCloud", 55),
                    ("Zvooq", 50),
                    ("Boom", 45),
                    ("YouTube Music", 70),
                    ("Tidal", 40)
                ]
            },
            "Видео": {
                "keywords": ["netflix", "youtube premium", "ivi", "okko", "start", "more.tv", "amediateka", "wink", "кинопоиск"],
                "popular_services": [
                    ("Netflix", 90),
                    ("YouTube Premium", 85),
                    ("Иви", 80),
                    ("Окко", 75),
                    ("Start", 70),
                    ("Кинопоиск", 65),
                    ("More.TV", 60),
                    ("Amediateka", 55),
                    ("Wink", 50),
                    ("MEGOGO", 45)
                ]
            },
            "Облако": {
                "keywords": ["yandex disk", "google drive", "dropbox", "mega", "icloud", "mail.ru облако"],
                "popular_services": [
                    ("Яндекс Диск", 85),
                    ("Google Drive", 80),
                    ("Dropbox", 75),
                    ("MEGA", 70),
                    ("iCloud", 65),
                    ("Облако Mail.ru", 60),
                    ("Box", 55),
                    ("pCloud", 50),
                    ("OneDrive", 75),
                    ("MediaFire", 45)
                ]
            },
            "Игры": {
                "keywords": ["xbox game pass", "playstation plus", "steam", "nintendo switch online", "ea play", "origin"],
                "popular_services": [
                    ("Xbox Game Pass", 85),
                    ("PlayStation Plus", 80),
                    ("Steam", 90),
                    ("Nintendo Switch Online", 70),
                    ("EA Play", 65),
                    ("Origin", 60),
                    ("Epic Games", 75),
                    ("Ubisoft+", 55),
                    ("GOG", 50),
                    ("GeForce NOW", 45)
                ]
            },
            "Образование": {
                "keywords": ["coursera", "udemy", "skillshare", "geekbrains", "stepik", "lingualeo"],
                "popular_services": [
                    ("Coursera", 85),
                    ("Udemy", 80),
                    ("Skillshare", 75),
                    ("GeekBrains", 70),
                    ("Stepik", 65),
                    ("Lingualeo", 60),
                    ("Skyeng", 70),
                    ("Яндекс Практикум", 75),
                    ("Нетология", 70),
                    ("Открытое образование", 55)
                ]
            },
            "Книги": {
                "keywords": ["литрес", "bookmate", "mybook", "аудиокниги"],
                "popular_services": [
                    ("Литрес", 85),
                    ("Bookmate", 80),
                    ("MyBook", 75),
                    ("Storytel", 70),
                    ("Аудиокниги", 65),
                    ("Читай-город", 60),
                    ("Лабиринт", 55),
                    ("Google Книги", 50),
                    ("Amazon Kindle", 70),
                    ("Apple Books", 65)
                ]
            },
            "Другие": {
                "keywords": [],
                "popular_services": [
                    ("Яндекс Плюс", 90),
                    ("Telegram Premium", 80),
                    ("Дзен", 70),
                    ("Яндекс Еда", 65),
                    ("Delivery Club", 60),
                    ("СберПрайм", 75),
                    ("Tinkoff Premium", 70),
                    ("Альфа-Банк Премиум", 65),
                    ("VK Donut", 55),
                    ("Boosty", 50)
                ]
            }
        }
    
    def get_popular_services_by_category(self, category: str, limit: int = 10) -> List[ServiceInfo]:
        """Получение популярных сервисов по категории"""
        category_data = self.categories.get(category)
        if not category_data:
            return []
        
        popular_services = []
        for service_name, popularity in category_data["popular_services"][:limit]:
            service_info = self.search_service(service_name)
            service_info.popularity = popularity
            popular_services.append(service_info)
        
        popular_services.sort(key=lambda x: x.popularity, reverse=True)
        return popular_services
    
    def search_by_category(self, category: str) -> List[ServiceInfo]:
        """Поиск сервисов по категории"""
        return self.get_popular_services_by_category(category)
    
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
            
            plans, url, payment_url = self.search_via_api(service_name)
            if plans:
                return ServiceInfo(
                    name=service_name,
                    category=category,
                    url=url,
                    plans=plans,
                    last_updated=datetime.now()
                )
            
            if not self.driver:
                if not self.init_browser():
                    return self.create_fallback_service(service_name)
            
            search_queries = [
                f"{service_name} подписка цена тарифы купить",
                f"{service_name} subscription price buy",
                f"{service_name} стоимость подписки оплата",
                f"купить подписку {service_name} 2024"
            ]
            
            for query in search_queries:
                try:
                    search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}&num=10"
                    self.driver.get(search_url)
                    time.sleep(2)
                    
                    page_source = self.driver.page_source
                    soup = BeautifulSoup(page_source, 'html.parser')
                    
                    organic_results = soup.find_all('div', {'class': ['g', 'MjjYud']})
                    
                    for result in organic_results[:5]:
                        link_elem = result.find('a', href=True)
                        if link_elem:
                            url = link_elem['href']
                            if '/url?q=' in url:
                                url = re.search(r'/url\?q=(https?://[^&]+)', url).group(1)
                            
                            if not any(domain in url for domain in ['google.', 'facebook.', 'twitter.']):
                                try:
                                    page_plans, payment_url = self.scrape_website(url)
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
            
            return self.search_popular_service(service_name)
            
        except Exception as e:
            print(f"Ошибка поиска: {e}")
            return self.create_fallback_service(service_name)
    
    def search_via_api(self, service_name: str):
        """Попробуем найти информацию через различные API"""
        try:
            ddg_url = f"https://api.duckduckgo.com/?q={service_name}+subscription+price+buy&format=json&pretty=1"
            response = self.session.get(ddg_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('Abstract'):
                    abstract = data['Abstract']
                    prices = re.findall(r'(\d+(?:\.\d+)?)\s*(?:руб|р\.?|₽|USD|\$)', abstract, re.IGNORECASE)
                    
                    if prices:
                        plans = []
                        payment_url = data.get('AbstractURL', '')
                        
                        for i, price in enumerate(prices[:3]):
                            try:
                                price_num = float(price)
                                plans.append(SubscriptionPlan(
                                    name=f"Тариф {i+1}",
                                    price_per_month=price_num,
                                    currency="RUB" if any(x in abstract.lower() for x in ['руб', 'р.', '₽']) else "USD",
                                    payment_url=payment_url
                                ))
                            except:
                                pass
                        
                        if plans:
                            return plans, payment_url, payment_url
        except:
            pass
        
        return None, None, None
    
    def scrape_website(self, url: str):
        """Парсим конкретный веб-сайт"""
        try:
            response = self.session.get(url, timeout=10)
            soup = BeautifulSoup(response.content, 'lxml')
            
            plans = []
            payment_url = None
            
            payment_buttons = soup.find_all(['a', 'button'], text=re.compile(r'купить|оформить|подписаться|buy|subscribe', re.IGNORECASE))
            if payment_buttons:
                for button in payment_buttons:
                    if button.name == 'a' and button.get('href'):
                        payment_url = button['href']
                        if not payment_url.startswith('http'):
                            payment_url = url + payment_url if url.endswith('/') else url + '/' + payment_url
                        break
            
            all_text = soup.get_text()
            
            price_patterns = [
                r'(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб\.?|₽|р\.?|RUB)\s*(?:в месяц|/мес|месяц|/мес\.)',
                r'(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:USD|\$|долл\.?)\s*(?:в месяц|/мес|month|/month)',
                r'цена[:\s\-]*(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб|₽|р|RUB)',
                r'стоимость[:\s\-]*(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:руб|₽|р|RUB)',
            ]
            
            for pattern in price_patterns:
                matches = re.finditer(pattern, all_text, re.IGNORECASE | re.DOTALL)
                for match in matches:
                    try:
                        price_str = match.group(1).replace(' ', '').replace(',', '.')
                        price = float(price_str)
                        
                        currency = "RUB" if any(x in match.group(0).lower() for x in ['руб', '₽', 'р', 'rub']) else "USD"
                        
                        start = max(0, match.start() - 200)
                        end = min(len(all_text), match.end() + 200)
                        context = all_text[start:end]
                        
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
                        
                        features = []
                        feature_keywords = [
                            'без рекламы', 'скачивание', 'офлайн', 'качество',
                            'устройств', 'профиль', '4k', 'hd', 'full hd'
                        ]
                        
                        for feature in feature_keywords:
                            if feature in context.lower():
                                features.append(feature.capitalize())
                        
                        features = features[:5]
                        
                        if not any(abs(p.price_per_month - price) < 0.01 for p in plans):
                            plans.append(SubscriptionPlan(
                                name=name,
                                price_per_month=price,
                                currency=currency,
                                features=features if features else None,
                                payment_url=payment_url
                            ))
                            
                    except Exception as e:
                        print(f"Ошибка обработки цены: {e}")
                        continue
            
            return plans[:5], payment_url
            
        except Exception as e:
            print(f"Ошибка парсинга сайта: {e}")
            return None, None
    
    def search_popular_service(self, service_name: str):
        """Прямой поиск для популярных сервисов"""
        service_name_lower = service_name.lower()
        
        service_pages = {
            'netflix': ('https://www.netflix.com/ru/', 'https://www.netflix.com/signup'),
            'spotify': ('https://www.spotify.com/ru-ru/premium/', 'https://www.spotify.com/ru-ru/purchase/offer/default-trial-1m/'),
            'yandex plus': ('https://plus.yandex.ru/', 'https://plus.yandex.ru/payment'),
            'яндекс плюс': ('https://plus.yandex.ru/', 'https://plus.yandex.ru/payment'),
            'youtube premium': ('https://www.youtube.com/premium', 'https://www.youtube.com/paid_memberships'),
            'ivi': ('https://www.ivi.ru/subscribe', 'https://www.ivi.ru/profile/subscription'),
            'okko': ('https://okko.tv/', 'https://okko.tv/payment'),
            'start': ('https://start.ru/', 'https://start.ru/payment'),
            'litres': ('https://www.litres.ru/', 'https://www.litres.ru/subscription/'),
            'bookmate': ('https://ru.bookmate.com/', 'https://ru.bookmate.com/pay'),
        }
        
        for key, (url, payment_url) in service_pages.items():
            if key in service_name_lower:
                try:
                    plans, found_payment_url = self.scrape_website(url)
                    if plans:
                        final_payment_url = found_payment_url or payment_url
                        for plan in plans:
                            plan.payment_url = final_payment_url
                        
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
        
        for category, data in self.categories.items():
            for keyword in data["keywords"]:
                if keyword in name_lower:
                    return category
        
        return "Другие"
    
    def get_fallback_plans(self, service_name: str) -> List[SubscriptionPlan]:
        name_lower = service_name.lower()
        
        service_plans = {
            'netflix': [
                SubscriptionPlan("С подлинками", 599, "RUB", 
                               features=["1 устройство", "Мобильное"],
                               payment_url="https://www.netflix.com/signup"),
                SubscriptionPlan("Стандартный", 799, "RUB", 
                               features=["2 устройства", "HD"],
                               payment_url="https://www.netflix.com/signup"),
                SubscriptionPlan("Премиум", 1299, "RUB", 
                               features=["4 устройства", "Ultra HD"],
                               payment_url="https://www.netflix.com/signup")
            ],
            'spotify': [
                SubscriptionPlan("Индивидуальный", 269, "RUB", 
                               features=["Без рекламы", "Скачивание"],
                               payment_url="https://www.spotify.com/ru-ru/purchase/offer/default-trial-1m/"),
                SubscriptionPlan("Дуэт", 349, "RUB", 
                               features=["2 аккаунта"],
                               payment_url="https://www.spotify.com/ru-ru/purchase/offer/duo-1m/"),
                SubscriptionPlan("Семейный", 449, "RUB", 
                               features=["До 6 аккаунтов", "Родительский контроль"],
                               payment_url="https://www.spotify.com/ru-ru/purchase/offer/family-1m/")
            ],
            'youtube premium': [
                SubscriptionPlan("Индивидуальный", 399, "RUB", 
                               features=["Без рекламы", "YouTube Music"],
                               payment_url="https://www.youtube.com/paid_memberships"),
                SubscriptionPlan("Семейный", 699, "RUB", 
                               features=["До 6 человек", "Родительский контроль"],
                               payment_url="https://www.youtube.com/paid_memberships")
            ],
            'яндекс плюс': [
                SubscriptionPlan("Яндекс Плюс", 249, "RUB", 
                               features=["Музыка", "Кино", "Кэшбэк"],
                               payment_url="https://plus.yandex.ru/payment"),
                SubscriptionPlan("Мульти", 449, "RUB", 
                               features=["До 5 человек", "Детский режим"],
                               payment_url="https://plus.yandex.ru/payment")
            ],
        }
        
        for key, plans in service_plans.items():
            if key in name_lower:
                return plans
        
        return [
            SubscriptionPlan("Базовый", 199, "RUB", 
                           features=["Базовый доступ"],
                           payment_url=f"https://www.google.com/search?q={service_name.replace(' ', '+')}+купить+подписку"),
            SubscriptionPlan("Стандарт", 399, "RUB", 
                           features=["Полный доступ"],
                           payment_url=f"https://www.google.com/search?q={service_name.replace(' ', '+')}+купить+подписку"),
            SubscriptionPlan("Премиум", 799, "RUB", 
                           features=["Все возможности"],
                           payment_url=f"https://www.google.com/search?q={service_name.replace(' ', '+')}+купить+подписку")
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

class CategorySearchThread(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    
    def __init__(self, category: str):
        super().__init__()
        self.category = category
    
    def run(self):
        try:
            self.progress.emit(f"Ищем популярные сервисы в категории {self.category}...")
            
            if not SELENIUM_AVAILABLE:
                raise Exception("Установите зависимости")
            
            searcher = SmartSearcher()
            services = searcher.search_by_category(self.category)
            searcher.close()
            
            self.finished.emit(services)
        except Exception as e:
            self.error.emit(str(e))

class EmailScanThread(QThread):
    """Поток для сканирования почты с выводом прогресса"""
    finished = pyqtSignal(list, list)  # подписки, последние сообщения
    error = pyqtSignal(str)
    progress = pyqtSignal(str)
    message_processed = pyqtSignal(dict)  # информация о обработанном сообщении
    
    def __init__(self, email: str, password: str):
        super().__init__()
        self.email = email
        self.password = password
    
    def run(self):
        try:
            self.progress.emit("Подключаемся к почтовому серверу...")
            time.sleep(1)
            
            scanner = EmailScanner()
            subscriptions, last_messages = scanner.scan_for_subscriptions(self.email, self.password)
            
            for msg in last_messages:
                self.message_processed.emit(msg)
                time.sleep(0.05)
            
            self.progress.emit(f"Сканирование завершено. Обработано {len(last_messages)} писем")
            self.finished.emit(subscriptions, last_messages)
            
        except Exception as e:
            error_msg = str(e)
            print(f"Ошибка в потоке сканирования: {error_msg}")
            self.error.emit(error_msg)

class EmailScanDialog(QDialog):
    """Диалог сканирования почты с отображением прогресса"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📧 Сканирование почты")
        self.setGeometry(400, 300, 800, 600)
        self.subscriptions_found = []
        self.last_messages = []
        
        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout()
        
        title = QLabel("<h2>🔍 Сканирование почты на подписки</h2>")
        layout.addWidget(title)
        
        self.status_label = QLabel("Подготовка к сканированию...")
        self.status_label.setStyleSheet("font-weight: bold; color: #3498db;")
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        layout.addWidget(QLabel("<hr>"))
        
        layout.addWidget(QLabel("<b>📨 Последние обработанные сообщения:</b>"))
        
        self.messages_list = QListWidget()
        self.messages_list.setMinimumHeight(200)
        layout.addWidget(self.messages_list)
        
        layout.addWidget(QLabel("<hr>"))
        
        layout.addWidget(QLabel("<b>✅ Найденные подписки:</b>"))
        
        self.subscriptions_list = QListWidget()
        self.subscriptions_list.setMinimumHeight(150)
        layout.addWidget(self.subscriptions_list)
        
        btn_layout = QHBoxLayout()
        
        self.cancel_btn = QPushButton("❌ Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        self.finish_btn = QPushButton("✅ Завершить")
        self.finish_btn.clicked.connect(self.accept)
        self.finish_btn.setEnabled(False)
        self.finish_btn.setStyleSheet("background-color: #2ecc71; color: white;")
        btn_layout.addWidget(self.finish_btn)
        
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
    
    def update_status(self, message: str):
        self.status_label.setText(message)
        QApplication.processEvents()
    
    def add_processed_message(self, message_info: dict):
        subject = message_info.get('subject', 'Без темы')
        from_addr = message_info.get('from', 'Неизвестно')
        is_subscription = message_info.get('is_subscription', False)
        
        if is_subscription:
            service = message_info.get('service', '')
            item_text = f"🔔 Найдена подписка: {service}\n📧 От: {from_addr}\n📝 Тема: {subject}"
            item = QListWidgetItem(item_text)
            item.setForeground(QColor("#27ae60"))
        else:
            item_text = f"📧 От: {from_addr}\n📝 Тема: {subject}"
            item = QListWidgetItem(item_text)
        
        self.messages_list.addItem(item)
        self.messages_list.scrollToBottom()
        
        current = self.messages_list.count()
        self.progress_bar.setValue(min(current * 5, 100))
    
    def add_subscription(self, subscription: dict):
        service_name = subscription.get('service_name', 'Неизвестный сервис')
        price = subscription.get('price', '?')
        currency = subscription.get('currency', 'RUB')
        
        if price:
            item_text = f"💰 {service_name}: {price} {currency}/мес"
        else:
            item_text = f"📋 {service_name}: цена не указана"
        
        item = QListWidgetItem(item_text)
        item.setForeground(QColor("#2ecc71"))
        self.subscriptions_list.addItem(item)
        
        self.subscriptions_found.append(subscription)
    
    def scan_completed(self, subscriptions: list, last_messages: list):
        self.update_status(f"✅ Сканирование завершено! Найдено {len(subscriptions)} подписок")
        self.progress_bar.setValue(100)
        self.finish_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        for sub in subscriptions:
            self.add_subscription(sub)

class RegistrationDialog(QDialog):
    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.setWindowTitle("Регистрация / Вход")
        self.setGeometry(400, 300, 400, 400)
        
        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout()
        
        self.tab_widget = QTabWidget()
        
        login_tab = self.create_login_tab()
        self.tab_widget.addTab(login_tab, "Вход")
        
        register_tab = self.create_register_tab()
        self.tab_widget.addTab(register_tab, "Регистрация")
        
        layout.addWidget(self.tab_widget)
        
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)
        
        self.setLayout(layout)
    
    def create_login_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("<h3>Вход в аккаунт</h3>"))
        
        self.login_email = QLineEdit()
        self.login_email.setPlaceholderText("Email")
        layout.addWidget(self.login_email)
        
        self.login_password = QLineEdit()
        self.login_password.setPlaceholderText("Пароль")
        self.login_password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.login_password)
        
        login_btn = QPushButton("Войти")
        login_btn.clicked.connect(self.login)
        layout.addWidget(login_btn)
        
        layout.addStretch()
        tab.setLayout(layout)
        return tab
    
    def create_register_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        
        layout.addWidget(QLabel("<h3>Регистрация</h3>"))
        
        self.register_username = QLineEdit()
        self.register_username.setPlaceholderText("Имя пользователя")
        layout.addWidget(self.register_username)
        
        self.register_email = QLineEdit()
        self.register_email.setPlaceholderText("Email")
        layout.addWidget(self.register_email)
        
        self.register_password = QLineEdit()
        self.register_password.setPlaceholderText("Пароль")
        self.register_password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.register_password)
        
        self.register_confirm_password = QLineEdit()
        self.register_confirm_password.setPlaceholderText("Подтвердите пароль")
        self.register_confirm_password.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.register_confirm_password)
        
        register_btn = QPushButton("Зарегистрироваться")
        register_btn.clicked.connect(self.register)
        layout.addWidget(register_btn)
        
        layout.addStretch()
        tab.setLayout(layout)
        return tab
    
    def login(self):
        email = self.login_email.text().strip()
        password = self.login_password.text()
        
        if not email or not password:
            QMessageBox.warning(self, "Ошибка", "Заполните все поля")
            return
        
        user_id = self.user_manager.login_user(email, password)
        if user_id:
            self.parent().current_user_id = user_id
            self.parent().current_user_email = email
            self.parent().update_user_info()
            QMessageBox.information(self, "Успех", "Вход выполнен успешно!")
            self.accept()
        else:
            QMessageBox.warning(self, "Ошибка", "Неверный email или пароль")
    
    def register(self):
        username = self.register_username.text().strip()
        email = self.register_email.text().strip()
        password = self.register_password.text()
        confirm_password = self.register_confirm_password.text()
        
        if not all([username, email, password, confirm_password]):
            QMessageBox.warning(self, "Ошибка", "Заполните все поля")
            return
        
        if password != confirm_password:
            QMessageBox.warning(self, "Ошибка", "Пароли не совпадают")
            return
        
        if len(password) < 6:
            QMessageBox.warning(self, "Ошибка", "Пароль должен быть не менее 6 символов")
            return
        
        if self.user_manager.register_user(email, username, password):
            QMessageBox.information(self, "Успех", "Регистрация успешна! Теперь войдите в аккаунт.")
            self.tab_widget.setCurrentIndex(0)
            self.login_email.setText(email)
            self.login_password.clear()
        else:
            QMessageBox.warning(self, "Ошибка", "Пользователь с таким email уже существует")

class ServiceCard(QFrame):
    def __init__(self, service_info, user_id=None, user_manager=None):
        super().__init__()
        self.service_info = service_info
        self.user_id = user_id
        self.user_manager = user_manager
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
        
        if self.user_id and self.user_manager:
            action_layout = QHBoxLayout()
            
            add_fav_btn = QPushButton("⭐ В избранное")
            add_fav_btn.clicked.connect(self.add_to_favorites)
            add_fav_btn.setStyleSheet("background-color: #f39c12;")
            action_layout.addWidget(add_fav_btn)
            
            action_layout.addStretch()
            layout.addLayout(action_layout)
        
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
        
        name_label = QLabel(f"<b>{plan.name}</b>")
        name_label.setStyleSheet("color: #2c3e50; font-size: 12pt;")
        layout.addWidget(name_label)
        
        price_text = f"💰 Цена: {plan.price_per_month} {plan.currency}/мес"
        if plan.currency == "RUB":
            price_text += f" (≈{plan.price_per_month * 0.011:.2f} $)"
        price_label = QLabel(price_text)
        price_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        layout.addWidget(price_label)
        
        if plan.features:
            features_label = QLabel("📋 Включено:")
            features_label.setStyleSheet("color: #34495e;")
            layout.addWidget(features_label)
            
            for feature in plan.features:
                feature_item = QLabel(f"   • {feature}")
                feature_item.setStyleSheet("color: #7f8c8d;")
                layout.addWidget(feature_item)
        
        if plan.payment_url:
            payment_btn = QPushButton("💳 Перейти к оплате")
            payment_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2ecc71;
                    color: white;
                    font-weight: bold;
                    padding: 8px;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #27ae60;
                }
            """)
            payment_btn.clicked.connect(lambda: self.open_payment_url(plan.payment_url))
            layout.addWidget(payment_btn)
        
        if self.user_id and self.user_manager:
            add_sub_btn = QPushButton("➕ Добавить в мои подписки")
            add_sub_btn.setStyleSheet("background-color: #3498db;")
            add_sub_btn.clicked.connect(lambda: self.add_subscription(plan))
            layout.addWidget(add_sub_btn)
        
        widget.setLayout(layout)
        return widget
    
    def open_payment_url(self, url):
        QDesktopServices.openUrl(QUrl(url))
    
    def add_to_favorites(self):
        if self.user_manager.add_favorite_service(self.user_id, self.service_info.name, self.service_info.category):
            QMessageBox.information(self, "Успех", f"Сервис {self.service_info.name} добавлен в избранное")
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось добавить в избранное")
    
    def add_subscription(self, plan):
        if self.user_manager.save_subscription(self.user_id, self.service_info, plan):
            QMessageBox.information(self, "Успех", f"Подписка на {self.service_info.name} добавлена")
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось добавить подписку")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.user_manager = UserManager()
        self.email_scanner = EmailScanner()
        self.current_user_id = None
        self.current_user_email = None
        self.services = []
        self.initUI()
    
    def initUI(self):
        self.setWindowTitle('Анализатор подписок v3.0')
        self.setGeometry(100, 100, 1400, 800)
        
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)
        
        self.main_tab = self.create_main_tab()
        self.tab_widget.addTab(self.main_tab, "🔍 Поиск")
        
        self.category_tab = self.create_category_tab()
        self.tab_widget.addTab(self.category_tab, "📂 По категориям")
        
        self.user_tab = self.create_user_tab()
        self.tab_widget.addTab(self.user_tab, "👤 Мой аккаунт")
        
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готово к поиску")
    
    def create_main_tab(self):
        tab = QWidget()
        main_layout = QHBoxLayout(tab)
        
        left_panel = self.create_left_panel()
        main_layout.addWidget(left_panel)
        
        right_panel = self.create_right_panel()
        main_layout.addWidget(right_panel, 1)
        
        return tab
    
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
        
        title = QLabel("<h1 style='color: #2c3e50;'>🎯 Анализатор подписок</h1>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        self.user_info_label = QLabel("👤 Гость")
        self.user_info_label.setStyleSheet("color: #7f8c8d; font-weight: bold;")
        layout.addWidget(self.user_info_label)
        
        account_layout = QHBoxLayout()
        
        self.login_btn = QPushButton("Войти")
        self.login_btn.clicked.connect(self.show_registration_dialog)
        self.login_btn.setStyleSheet("background-color: #3498db;")
        account_layout.addWidget(self.login_btn)
        
        self.logout_btn = QPushButton("Выйти")
        self.logout_btn.clicked.connect(self.logout)
        self.logout_btn.setStyleSheet("background-color: #e74c3c;")
        self.logout_btn.setVisible(False)
        account_layout.addWidget(self.logout_btn)
        
        layout.addLayout(account_layout)
        
        layout.addWidget(QLabel("<hr>"))
        
        layout.addWidget(QLabel("<b>🔍 Введите название сервиса или категории:</b>"))
        self.service_input = QLineEdit()
        self.service_input.setPlaceholderText("Например: Netflix, Музыка, Игры...")
        layout.addWidget(self.service_input)
        
        self.search_btn = QPushButton("🚀 Найти информацию")
        self.search_btn.clicked.connect(self.start_search)
        layout.addWidget(self.search_btn)
        
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #7f8c8d; font-style: italic;")
        layout.addWidget(self.progress_label)
        
        if not SELENIUM_AVAILABLE:
            warning = QLabel("⚠️ Установите зависимости!")
            warning.setStyleSheet("color: #e74c3c; font-weight: bold;")
            layout.addWidget(warning)
            install_btn = QPushButton("📦 Установить зависимости")
            install_btn.clicked.connect(self.install_deps)
            layout.addWidget(install_btn)
        
        layout.addWidget(QLabel("<hr>"))
        
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
        
        layout.addWidget(QLabel("<hr>"))
        
        layout.addWidget(QLabel("<b>📋 Найденные сервисы:</b>"))
        
        self.services_list = QListWidget()
        self.services_list.itemDoubleClicked.connect(self.show_service_details)
        layout.addWidget(self.services_list)
        
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
        
        self.stats_label = QLabel("Найдено: 0 сервисов")
        self.stats_label.setStyleSheet("color: #7f8c8d; font-size: 10pt;")
        layout.addWidget(self.stats_label)
        
        layout.addStretch()
        return panel
    
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
                <h1 style='color: #7f8c8d;'>🎯 Анализатор подписок v3.0</h1>
                <p style='color: #95a5a6; font-size: 14pt;'>
                    Введите название сервиса для поиска информации о подписках<br>
                    Или введите категорию (Музыка, Видео, Игры и т.д.)
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
    
    def create_category_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        layout.addWidget(QLabel("<h2>🔍 Поиск по категориям</h2>"))
        layout.addWidget(QLabel("Выберите категорию для просмотра популярных подписок:"))
        
        categories_layout = QGridLayout()
        categories = [
            ("Музыка", "🎵", "#e74c3c"),
            ("Видео", "🎬", "#3498db"),
            ("Облако", "☁️", "#2ecc71"),
            ("Игры", "🎮", "#9b59b6"),
            ("Образование", "📚", "#f39c12"),
            ("Книги", "📖", "#1abc9c"),
            ("Другие", "⭐", "#95a5a6")
        ]
        
        row, col = 0, 0
        for category_name, icon, color in categories:
            btn = QPushButton(f"{icon} {category_name}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color};
                    color: white;
                    padding: 20px;
                    font-size: 14pt;
                    font-weight: bold;
                    border-radius: 10px;
                }}
                QPushButton:hover {{
                    background-color: {color};
                    opacity: 0.9;
                }}
            """)
            btn.clicked.connect(lambda checked, cat=category_name: self.search_by_category(cat))
            categories_layout.addWidget(btn, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1
        
        layout.addLayout(categories_layout)
        
        self.category_results_area = QScrollArea()
        self.category_results_area.setWidgetResizable(True)
        self.category_results_widget = QWidget()
        self.category_results_layout = QVBoxLayout(self.category_results_widget)
        self.category_results_area.setWidget(self.category_results_widget)
        
        layout.addWidget(QLabel("<h3>Результаты:</h3>"))
        layout.addWidget(self.category_results_area, 1)
        
        return tab
    
    def create_user_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        self.user_profile_label = QLabel("<h2>👤 Мой профиль</h2>")
        layout.addWidget(self.user_profile_label)
        
        user_btn_layout = QHBoxLayout()
        
        scan_email_btn = QPushButton("📧 Сканировать почту на подписки")
        scan_email_btn.clicked.connect(self.scan_email_for_subscriptions)
        scan_email_btn.setStyleSheet("background-color: #3498db;")
        user_btn_layout.addWidget(scan_email_btn)
        
        export_btn = QPushButton("📊 Экспорт подписок")
        export_btn.clicked.connect(self.export_subscriptions)
        export_btn.setStyleSheet("background-color: #2ecc71;")
        user_btn_layout.addWidget(export_btn)
        
        layout.addLayout(user_btn_layout)
        
        user_tabs = QTabWidget()
        
        self.my_subscriptions_tab = self.create_my_subscriptions_tab()
        user_tabs.addTab(self.my_subscriptions_tab, "📋 Мои подписки")
        
        self.favorites_tab = self.create_favorites_tab()
        user_tabs.addTab(self.favorites_tab, "⭐ Избранное")
        
        self.stats_tab = self.create_stats_tab()
        user_tabs.addTab(self.stats_tab, "📈 Статистика")
        
        layout.addWidget(user_tabs, 1)
        
        return tab
    
    def create_my_subscriptions_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        self.subscriptions_list = QTableWidget()
        self.subscriptions_list.setColumnCount(8)
        self.subscriptions_list.setHorizontalHeaderLabels([
            "Сервис", "Тариф", "Цена", "Валюта", "Начало", "Продление", "Статус", "Действия"
        ])
        self.subscriptions_list.horizontalHeader().setStretchLastSection(True)
        
        layout.addWidget(self.subscriptions_list)
        
        btn_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("🔄 Обновить")
        refresh_btn.clicked.connect(self.load_user_subscriptions)
        btn_layout.addWidget(refresh_btn)
        
        total_label = QLabel("Итого в месяц: 0 RUB")
        total_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        btn_layout.addWidget(total_label)
        self.total_monthly_label = total_label
        
        layout.addLayout(btn_layout)
        
        return tab
    
    def create_favorites_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        self.favorites_list = QListWidget()
        layout.addWidget(self.favorites_list)
        
        btn_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("🔄 Обновить")
        refresh_btn.clicked.connect(self.load_favorites)
        btn_layout.addWidget(refresh_btn)
        
        clear_btn = QPushButton("🗑️ Очистить избранное")
        clear_btn.clicked.connect(self.clear_favorites)
        clear_btn.setStyleSheet("background-color: #e74c3c;")
        btn_layout.addWidget(clear_btn)
        
        layout.addLayout(btn_layout)
        
        return tab
    
    def create_stats_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        layout.addWidget(self.stats_text)
        
        return tab
    
    def update_user_info(self):
        if self.current_user_id:
            self.user_info_label.setText(f"👤 {self.current_user_email}")
            self.login_btn.setVisible(False)
            self.logout_btn.setVisible(True)
            self.user_profile_label.setText(f"<h2>👤 Профиль: {self.current_user_email}</h2>")
            
            self.load_user_subscriptions()
            self.load_favorites()
            self.update_stats()
        else:
            self.user_info_label.setText("👤 Гость")
            self.login_btn.setVisible(True)
            self.logout_btn.setVisible(False)
            self.user_profile_label.setText("<h2>👤 Мой профиль</h2>")
    
    def show_registration_dialog(self):
        dialog = RegistrationDialog(self.user_manager, self)
        if dialog.exec_():
            self.update_user_info()
    
    def logout(self):
        self.current_user_id = None
        self.current_user_email = None
        self.update_user_info()
        QMessageBox.information(self, "Выход", "Вы вышли из аккаунта")
    
    def load_user_subscriptions(self):
        if not self.current_user_id:
            return
        
        subscriptions = self.user_manager.get_user_subscriptions(self.current_user_id)
        self.subscriptions_list.setRowCount(len(subscriptions))
        
        total_monthly = 0
        
        for i, sub in enumerate(subscriptions):
            self.subscriptions_list.setItem(i, 0, QTableWidgetItem(sub['service_name']))
            self.subscriptions_list.setItem(i, 1, QTableWidgetItem(sub['plan_name'] or ''))
            self.subscriptions_list.setItem(i, 2, QTableWidgetItem(str(sub['price'] or '')))
            self.subscriptions_list.setItem(i, 3, QTableWidgetItem(sub['currency'] or ''))
            self.subscriptions_list.setItem(i, 4, QTableWidgetItem(sub['start_date'] or ''))
            self.subscriptions_list.setItem(i, 5, QTableWidgetItem(sub['renewal_date'] or ''))
            self.subscriptions_list.setItem(i, 6, QTableWidgetItem(sub['status'] or ''))
            
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            
            if sub['payment_url']:
                payment_btn = QPushButton("💳")
                payment_btn.setToolTip("Перейти к оплате")
                payment_btn.clicked.connect(lambda checked, url=sub['payment_url']: QDesktopServices.openUrl(QUrl(url)))
                action_layout.addWidget(payment_btn)
            
            delete_btn = QPushButton("🗑️")
            delete_btn.setToolTip("Удалить подписку")
            delete_btn.setStyleSheet("background-color: #e74c3c; color: white;")
            delete_btn.clicked.connect(lambda checked, s=sub['service_name']: self.delete_subscription(s))
            action_layout.addWidget(delete_btn)
            
            action_layout.setContentsMargins(0, 0, 0, 0)
            self.subscriptions_list.setCellWidget(i, 7, action_widget)
            
            if sub['price']:
                if sub['currency'] == 'USD':
                    total_monthly += sub['price'] * 90
                else:
                    total_monthly += sub['price']
        
        self.total_monthly_label.setText(f"Итого в месяц: {total_monthly:.2f} RUB")
        self.subscriptions_list.resizeColumnsToContents()
    
    def delete_subscription(self, service_name):
        reply = QMessageBox.question(self, "Подтверждение", 
                                   f"Удалить подписку на {service_name}?", 
                                   QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            if self.user_manager.delete_subscription(self.current_user_id, service_name):
                QMessageBox.information(self, "Успех", "Подписка удалена")
                self.load_user_subscriptions()
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось удалить подписку")
    
    def load_favorites(self):
        if not self.current_user_id:
            self.favorites_list.clear()
            return
        
        favorites = self.user_manager.get_favorite_services(self.current_user_id)
        self.favorites_list.clear()
        
        for fav in favorites:
            item = QListWidgetItem(f"⭐ {fav['service_name']} ({fav['category']})")
            item.setData(Qt.UserRole, fav['service_name'])
            self.favorites_list.addItem(item)
    
    def clear_favorites(self):
        if not self.current_user_id:
            return
        
        reply = QMessageBox.question(self, "Подтверждение", 
                                   "Очистить все избранное?", 
                                   QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            if self.user_manager.clear_favorites(self.current_user_id):
                self.favorites_list.clear()
                QMessageBox.information(self, "Успех", "Избранное очищено")
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось очистить избранное")
    
    def update_stats(self):
        if not self.current_user_id:
            self.stats_text.clear()
            return
        
        subscriptions = self.user_manager.get_user_subscriptions(self.current_user_id)
        favorites = self.user_manager.get_favorite_services(self.current_user_id)
        
        stats_text = "<h3>📊 Статистика</h3>"
        stats_text += f"<b>Всего подписок:</b> {len(subscriptions)}<br>"
        stats_text += f"<b>В избранном:</b> {len(favorites)}<br><br>"
        
        if subscriptions:
            total_monthly = sum(sub['price'] or 0 for sub in subscriptions)
            stats_text += f"<b>Общая стоимость в месяц:</b> {total_monthly:.2f} RUB<br>"
            
            categories = {}
            for sub in subscriptions:
                category = self.detect_category(sub['service_name'])
                categories[category] = categories.get(category, 0) + 1
            
            stats_text += "<br><b>Распределение по категориям:</b><br>"
            for category, count in categories.items():
                stats_text += f"  {category}: {count} подписок<br>"
        
        self.stats_text.setHtml(stats_text)
    
    def detect_category(self, service_name: str) -> str:
        name_lower = service_name.lower()
        
        category_keywords = {
            "Музыка": ["spotify", "yandex music", "apple music", "музык", "soundcloud", "deezer"],
            "Видео": ["netflix", "youtube", "ivi", "okko", "start", "кино", "видео"],
            "Игры": ["steam", "xbox", "playstation", "nintendo", "игр", "game"],
            "Облако": ["google drive", "yandex disk", "dropbox", "облак", "disk", "drive"],
            "Книги": ["litres", "bookmate", "mybook", "книг", "book"],
            "Образование": ["coursera", "udemy", "skillshare", "курс", "обучен"]
        }
        
        for category, keywords in category_keywords.items():
            for keyword in keywords:
                if keyword in name_lower:
                    return category
        
        return "Другие"
    
    def scan_email_for_subscriptions(self):
        if not self.current_user_id:
            QMessageBox.warning(self, "Ошибка", "Сначала войдите в аккаунт")
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Сканирование почты")
        dialog.setGeometry(400, 300, 450, 300)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #f8f9fa;
            }
            QLabel {
                color: #2c3e50;
            }
            QPushButton {
                padding: 10px;
                border-radius: 5px;
                font-weight: bold;
            }
        """)
        
        layout = QVBoxLayout(dialog)
        
        title_label = QLabel("<h3>🔐 Введите пароль от почты</h3>")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        email_frame = QFrame()
        email_frame.setStyleSheet("""
            QFrame {
                background-color: #e8f4fc;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        email_layout = QHBoxLayout(email_frame)
        email_layout.addWidget(QLabel("📧"))
        email_label = QLabel(f"<b>{self.current_user_email}</b>")
        email_label.setStyleSheet("color: #2c3e50; font-size: 12pt;")
        email_layout.addWidget(email_label)
        email_layout.addStretch()
        layout.addWidget(email_frame)
        
        password_frame = QFrame()
        password_frame.setStyleSheet("""
            QFrame {
                background-color: white;
                border-radius: 8px;
                padding: 15px;
                border: 2px solid #dfe6e9;
            }
        """)
        password_layout = QVBoxLayout(password_frame)
        
        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.Password)
        password_input.setPlaceholderText("Введите пароль от почты")
        password_input.setStyleSheet("""
            QLineEdit {
                padding: 12px;
                border: 2px solid #bdc3c7;
                border-radius: 6px;
                font-size: 12pt;
            }
            QLineEdit:focus {
                border-color: #3498db;
            }
        """)
        password_layout.addWidget(password_input)
        
        show_password = QCheckBox("Показать пароль")
        show_password.setStyleSheet("color: #7f8c8d;")
        show_password.stateChanged.connect(lambda state: password_input.setEchoMode(QLineEdit.Normal if state else QLineEdit.Password))
        password_layout.addWidget(show_password)
        
        layout.addWidget(password_frame)
        
        warning_frame = QFrame()
        warning_frame.setStyleSheet("""
            QFrame {
                background-color: #fff3cd;
                border: 1px solid #ffeaa7;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        warning_layout = QVBoxLayout(warning_frame)
        
        warning1 = QLabel("⚠️ Пароль используется только для сканирования и не сохраняется")
        warning1.setStyleSheet("color: #856404; font-size: 10pt;")
        warning1.setWordWrap(True)
        warning_layout.addWidget(warning1)
        
        warning2 = QLabel("🔒 Все данные передаются по защищенному соединению")
        warning2.setStyleSheet("color: #856404; font-size: 10pt;")
        warning2.setWordWrap(True)
        warning_layout.addWidget(warning2)
        
        layout.addWidget(warning_frame)
        
        info_label = QLabel("📅 Будет просканировано 90 дней истории (последние 100 писем)")
        info_label.setStyleSheet("color: #7f8c8d; font-size: 10pt;")
        info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_label)
        
        btn_layout = QHBoxLayout()
        
        scan_btn = QPushButton("🔍 Начать сканирование")
        scan_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                font-size: 12pt;
                padding: 12px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
            }
        """)
        scan_btn.clicked.connect(lambda: self.start_email_scan(dialog, password_input.text()))
        btn_layout.addWidget(scan_btn)
        
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                padding: 12px;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
        
        def validate_password():
            password = password_input.text()
            if len(password) < 4:
                scan_btn.setEnabled(False)
                password_input.setStyleSheet("""
                    QLineEdit {
                        padding: 12px;
                        border: 2px solid #e74c3c;
                        border-radius: 6px;
                        font-size: 12pt;
                    }
                """)
            else:
                scan_btn.setEnabled(True)
                password_input.setStyleSheet("""
                    QLineEdit {
                        padding: 12px;
                        border: 2px solid #2ecc71;
                        border-radius: 6px;
                        font-size: 12pt;
                    }
                """)
        
        password_input.textChanged.connect(validate_password)
        validate_password()
        
        if dialog.exec_() == QDialog.Accepted:
            return
    
    def start_email_scan(self, dialog, password):
        if not password:
            QMessageBox.warning(dialog, "Ошибка", "Введите пароль от почты")
            return
        
        if len(password) < 4:
            QMessageBox.warning(dialog, "Ошибка", "Пароль слишком короткий")
            return
        
        dialog.accept()
        
        self.scan_dialog = EmailScanDialog(self)
        self.scan_dialog.show()
        
        self.email_thread = EmailScanThread(self.current_user_email, password)
        self.email_thread.message_processed.connect(self.scan_dialog.add_processed_message)
        self.email_thread.progress.connect(self.scan_dialog.update_status)
        self.email_thread.finished.connect(self.on_email_scan_finished)
        self.email_thread.error.connect(self.on_email_scan_error)
        self.email_thread.start()
    
    def on_email_scan_error(self, error_msg):
        print(f"Ошибка сканирования: {error_msg}")
        
        if self.scan_dialog:
            self.scan_dialog.reject()
        
        error_msg_lower = error_msg.lower()
        
        password_error_keywords = [
            'неправильный пароль',
            'неверный пароль',
            'invalid credentials',
            'authentication failed',
            'login failed',
            'ошибка аутентификации',
            'неправильные учетные данные',
            'password',
            'парол',
            'credentials',
            'auth',
            'аутентификац'
        ]
        
        connection_error_keywords = [
            'timeout',
            'connection',
            'подключен',
            'соединен',
            'ssl',
            'tls',
            'imap',
            'сервер'
        ]
        
        is_password_error = any(keyword in error_msg_lower for keyword in password_error_keywords)
        is_connection_error = any(keyword in error_msg_lower for keyword in connection_error_keywords)
        
        if is_password_error:
            title = "❌ Неправильный пароль"
            message = f"""
            <h3>Не удалось войти в почту</h3>
            <p><b>Причина:</b> Неправильный пароль от почтового ящика</p>
            <p><b>Ваша почта:</b> {self.current_user_email}</p>
            
            <h4>Что делать?</h4>
            <ol>
                <li>Проверьте правильность ввода пароля</li>
                <li>Убедитесь, что вводите пароль именно от почты, а не от аккаунта в программе</li>
                <li>Если забыли пароль, восстановите его через сайт почтового сервиса</li>
            </ol>
            
            <p style="color: #7f8c8d; font-size: 10pt;">
            ⚠️ Для Gmail может потребоваться использование "Пароля приложений", 
            если включена двухфакторная аутентификация
            </p>
            """
        elif is_connection_error:
            title = "⚠️ Ошибка подключения"
            message = f"""
            <h3>Не удалось подключиться к почтовому серверу</h3>
            <p><b>Причина:</b> {error_msg}</p>
            <p><b>Ваша почта:</b> {self.current_user_email}</p>
            
            <h4>Возможные причины:</h4>
            <ul>
                <li>Проблемы с интернет-соединением</li>
                <li>Почтовый сервер временно недоступен</li>
                <li>Необходимо разрешить доступ к почте из сторонних приложений</li>
                <li>Для Gmail может потребоваться включить IMAP в настройках почты</li>
            </ul>
            
            <h4>Что делать?</h4>
            <ol>
                <li>Проверьте подключение к интернету</li>
                <li>Попробуйте позже</li>
                <li>Для Gmail: проверьте настройки доступа IMAP</li>
                <li>Убедитесь, что почтовый сервер поддерживает IMAP</li>
            </ol>
            """
        else:
            title = "❌ Ошибка сканирования"
            message = f"""
            <h3>Произошла ошибка при сканировании почты</h3>
            <p><b>Ошибка:</b> {error_msg}</p>
            <p><b>Ваша почта:</b> {self.current_user_email}</p>
            
            <h4>Что делать?</h4>
            <ol>
                <li>Попробуйте еще раз</li>
                <li>Проверьте правильность ввода пароля</li>
                <li>Убедитесь, что почтовый ящик поддерживает IMAP</li>
                <li>Если проблема повторяется, попробуйте использовать другой почтовый сервис</li>
            </ol>
            """
        
        error_dialog = QDialog(self)
        error_dialog.setWindowTitle(title)
        error_dialog.setGeometry(400, 300, 500, 400)
        
        layout = QVBoxLayout(error_dialog)
        
        error_text = QTextEdit()
        error_text.setHtml(message)
        error_text.setReadOnly(True)
        error_text.setStyleSheet("""
            QTextEdit {
                background-color: white;
                border: 1px solid #dfe6e9;
                border-radius: 8px;
                padding: 15px;
                font-size: 11pt;
            }
        """)
        layout.addWidget(error_text)
        
        btn_layout = QHBoxLayout()
        
        if is_password_error:
            retry_btn = QPushButton("🔄 Попробовать с другим паролем")
            retry_btn.setStyleSheet("background-color: #3498db; color: white; padding: 10px;")
            retry_btn.clicked.connect(lambda: self.retry_email_scan(error_dialog))
            btn_layout.addWidget(retry_btn)
        
        close_btn = QPushButton("Закрыть")
        close_btn.setStyleSheet("background-color: #95a5a6; color: white; padding: 10px;")
        close_btn.clicked.connect(error_dialog.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
        
        error_dialog.exec_()
    
    def retry_email_scan(self, error_dialog):
        error_dialog.accept()
        self.scan_email_for_subscriptions()
    
    def on_email_scan_finished(self, subscriptions, last_messages):
        self.scan_dialog.scan_completed(subscriptions, last_messages)
        
        if subscriptions:
            saved_count = 0
            for sub in subscriptions:
                service_name = sub.get('service_name')
                if service_name:
                    service_info = ServiceInfo(
                        name=service_name,
                        category=self.detect_category(service_name),
                        url=f"https://www.google.com/search?q={service_name.replace(' ', '+')}",
                        plans=[SubscriptionPlan(
                            name="Основной",
                            price_per_month=sub.get('price', 0),
                            currency=sub.get('currency', 'RUB'),
                            payment_url=f"https://www.google.com/search?q={service_name.replace(' ', '+')}+купить+подписку"
                        )],
                        last_updated=datetime.now()
                    )
                    
                    if self.current_user_id:
                        if self.user_manager.save_subscription(self.current_user_id, service_info, service_info.plans[0]):
                            saved_count += 1
            
            self.load_user_subscriptions()
            
            if self.scan_dialog.exec_() == QDialog.Accepted:
                report_dialog = QDialog(self)
                report_dialog.setWindowTitle("📊 Отчет о сканировании")
                report_dialog.setGeometry(400, 300, 400, 300)
                
                layout = QVBoxLayout(report_dialog)
                
                success_label = QLabel("✅ Сканирование завершено успешно!")
                success_label.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 14pt;")
                success_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(success_label)
                
                stats_text = f"""
                <div style='padding: 20px; background-color: #f8f9fa; border-radius: 10px;'>
                    <h3>📈 Статистика сканирования:</h3>
                    <p>📨 Обработано писем: <b>{len(last_messages)}</b></p>
                    <p>💰 Найдено подписок: <b>{len(subscriptions)}</b></p>
                    <p>💾 Сохранено подписок: <b>{saved_count}</b></p>
                </div>
                """
                
                stats_label = QLabel(stats_text)
                stats_label.setWordWrap(True)
                layout.addWidget(stats_label)
                
                if subscriptions:
                    services_text = "<h4>🎯 Найденные сервисы:</h4><ul>"
                    for sub in subscriptions[:5]:
                        service = sub.get('service_name', 'Неизвестно')
                        price = sub.get('price', '?')
                        currency = sub.get('currency', 'RUB')
                        services_text += f"<li>{service}: {price} {currency}/мес</li>"
                    services_text += "</ul>"
                    
                    services_label = QLabel(services_text)
                    services_label.setWordWrap(True)
                    layout.addWidget(services_label)
                
                ok_btn = QPushButton("👍 Отлично!")
                ok_btn.setStyleSheet("background-color: #2ecc71; color: white; padding: 12px; font-size: 12pt;")
                ok_btn.clicked.connect(report_dialog.accept)
                layout.addWidget(ok_btn)
                
                report_dialog.exec_()
        else:
            self.scan_dialog.update_status("❌ Подписок не найдено")
            if self.scan_dialog.exec_() == QDialog.Accepted:
                QMessageBox.information(self, "Результат", 
                    f"Сканирование завершено.\n\n"
                    f"📨 Обработано писем: {len(last_messages)}\n"
                    f"💰 Подписок не найдено.\n\n"
                    f"ℹ️ Это может означать, что:\n"
                    f"• В вашей почте нет писем о подписках\n"
                    f"• Подписки оформлены на другую почту\n"
                    f"• Письма о подписках старше 90 дней")
    
    def export_subscriptions(self):
        if not self.current_user_id:
            QMessageBox.warning(self, "Ошибка", "Сначала войдите в аккаунт")
            return
        
        subscriptions = self.user_manager.get_user_subscriptions(self.current_user_id)
        if not subscriptions:
            QMessageBox.warning(self, "Ошибка", "Нет подписок для экспорта")
            return
        
        file_name, _ = QFileDialog.getSaveFileName(
            self, "Экспорт подписок", "my_subscriptions.csv", "CSV Files (*.csv)"
        )
        
        if file_name:
            try:
                with open(file_name, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=subscriptions[0].keys())
                    writer.writeheader()
                    writer.writerows(subscriptions)
                
                QMessageBox.information(self, "Успех", f"Подписки экспортированы в {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Ошибка экспорта: {e}")
    
    def start_search(self):
        query = self.service_input.text().strip()
        if not query:
            QMessageBox.warning(self, "Ошибка", "Введите запрос!")
            return
        
        searcher = SmartSearcher()
        if query in searcher.categories:
            self.search_by_category(query)
        else:
            self.search_service(query)
    
    def search_by_category(self, category):
        self.tab_widget.setCurrentIndex(1)
        
        for i in reversed(range(self.category_results_layout.count())):
            widget = self.category_results_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        
        loading_label = QLabel(f"🔍 Ищем популярные сервисы в категории '{category}'...")
        self.category_results_layout.addWidget(loading_label)
        
        self.category_thread = CategorySearchThread(category)
        self.category_thread.finished.connect(self.on_category_search_finished)
        self.category_thread.error.connect(self.on_search_error)
        self.category_thread.progress.connect(lambda msg: self.status_bar.showMessage(msg))
        self.category_thread.start()
    
    def on_category_search_finished(self, services):
        for i in reversed(range(self.category_results_layout.count())):
            widget = self.category_results_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        
        if not services:
            no_results = QLabel(f"❌ Не удалось найти сервисы в категории")
            self.category_results_layout.addWidget(no_results)
            return
        
        grid_layout = QGridLayout()
        row, col = 0, 0
        
        for service in services:
            card = ServiceCard(service, self.current_user_id, self.user_manager)
            grid_layout.addWidget(card, row, col)
            
            col += 1
            if col > 1:
                col = 0
                row += 1
        
        grid_widget = QWidget()
        grid_widget.setLayout(grid_layout)
        self.category_results_layout.addWidget(grid_widget)
        
        self.status_bar.showMessage(f"Найдено {len(services)} сервисов в категории")
    
    def search_service(self, service_name):
        self.search_btn.setEnabled(False)
        self.progress_label.setText(f"🔍 Ищем информацию о {service_name}...")
        
        self.thread = SearchThread(service_name)
        self.thread.finished.connect(self.on_search_finished)
        self.thread.error.connect(self.on_search_error)
        self.thread.progress.connect(self.progress_label.setText)
        self.thread.start()
    
    def on_search_finished(self, service_info):
        self.services.append(service_info)
        
        item_text = f"✅ {service_info.name}"
        if service_info.plans:
            min_price = min(p.price_per_month for p in service_info.plans)
            currency = service_info.plans[0].currency
            item_text += f" - от {min_price} {currency}"
        
        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, service_info)
        self.services_list.addItem(item)
        
        self.update_display()
        self.compare_btn.setEnabled(len(self.services) >= 2)
        
        self.stats_label.setText(f"Найдено: {len(self.services)} сервисов")
        
        self.search_btn.setEnabled(True)
        self.progress_label.setText("✅ Поиск завершен")
        self.status_bar.showMessage(f"Найден сервис: {service_info.name}")
    
    def on_search_error(self, error_msg):
        QMessageBox.critical(self, "Ошибка", f"Ошибка поиска: {error_msg}")
        self.search_btn.setEnabled(True)
        self.progress_label.setText("❌ Ошибка при поиске")
    
    def update_display(self):
        if self.placeholder:
            self.placeholder.setParent(None)
            self.placeholder = None
        
        for i in reversed(range(self.content_layout.count())):
            widget = self.content_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        
        for service in self.services:
            card = ServiceCard(service, self.current_user_id, self.user_manager)
            self.content_layout.addWidget(card)
        
        self.content_layout.addStretch()
    
    def set_example(self, example):
        self.service_input.setText(example)
        self.start_search()
    
    def show_service_details(self, item):
        service_info = item.data(Qt.UserRole)
        if service_info:
            dialog = ServiceDetailsDialog(service_info, self.current_user_id, self.user_manager, self)
            dialog.exec_()
    
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
            
            self.placeholder = QLabel("""
                <div style='text-align: center; padding: 50px;'>
                    <h1 style='color: #7f8c8d;'>🎯 Анализатор подписок v3.0</h1>
                    <p style='color: #95a5a6; font-size: 14pt;'>
                        Введите название сервиса для поиска информации о подписках<br>
                        Или введите категорию (Музыка, Видео, Игры и т.д.)
                    </p>
                    <p style='color: #bdc3c7;'>
                        Программа автоматически найдет актуальные тарифы и цены
                    </p>
                </div>
            """)
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.content_layout.addWidget(self.placeholder)
            
            self.status_bar.showMessage("Список очищен")
    
    def install_deps(self):
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", 
                                  "PyQt5", "selenium", "webdriver-manager", 
                                  "beautifulsoup4", "requests", "lxml"])
            QMessageBox.information(self, "Успех", "Зависимости установлены! Перезапустите программу.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось установить: {e}")

class ServiceDetailsDialog(QDialog):
    def __init__(self, service_info, user_id=None, user_manager=None, parent=None):
        super().__init__(parent)
        self.service_info = service_info
        self.user_id = user_id
        self.user_manager = user_manager
        self.setWindowTitle(f"Детали: {service_info.name}")
        self.setGeometry(300, 300, 600, 500)
        
        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout()
        
        info_group = QGroupBox("Информация о сервисе")
        info_layout = QVBoxLayout()
        
        info_layout.addWidget(QLabel(f"<b>Название:</b> {self.service_info.name}"))
        info_layout.addWidget(QLabel(f"<b>Категория:</b> {self.service_info.category}"))
        
        if self.service_info.url:
            url_label = QLabel(f"<b>URL:</b> <a href='{self.service_info.url}'>{self.service_info.url}</a>")
            url_label.setOpenExternalLinks(True)
            info_layout.addWidget(url_label)
        
        info_layout.addWidget(QLabel(f"<b>Обновлено:</b> {self.service_info.last_updated.strftime('%d.%m.%Y %H:%M')}"))
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        if self.service_info.plans:
            plans_group = QGroupBox("Тарифные планы")
            plans_layout = QVBoxLayout()
            
            for plan in self.service_info.plans:
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
                
                if plan.payment_url:
                    payment_btn = QPushButton("💳 Перейти к оплате")
                    payment_btn.setStyleSheet("background-color: #2ecc71; color: white;")
                    payment_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(plan.payment_url)))
                    plan_layout.addWidget(payment_btn)
                
                if self.user_id and self.user_manager:
                    add_btn = QPushButton("➕ Добавить в мои подписки")
                    add_btn.setStyleSheet("background-color: #3498db; color: white;")
                    add_btn.clicked.connect(lambda: self.add_subscription(plan))
                    plan_layout.addWidget(add_btn)
                
                plan_frame.setLayout(plan_layout)
                plans_layout.addWidget(plan_frame)
            
            plans_group.setLayout(plans_layout)
            layout.addWidget(plans_group)
        else:
            layout.addWidget(QLabel("❌ Тарифы не найдены"))
        
        btn_layout = QHBoxLayout()
        
        if self.user_id and self.user_manager:
            fav_btn = QPushButton("⭐ В избранное")
            fav_btn.setStyleSheet("background-color: #f39c12; color: white;")
            fav_btn.clicked.connect(self.add_to_favorites)
            btn_layout.addWidget(fav_btn)
        
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
    
    def add_subscription(self, plan):
        if self.user_manager.save_subscription(self.user_id, self.service_info, plan):
            QMessageBox.information(self, "Успех", f"Подписка на {self.service_info.name} добавлена")
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось добавить подписку")
    
    def add_to_favorites(self):
        if self.user_manager.add_favorite_service(self.user_id, self.service_info.name, self.service_info.category):
            QMessageBox.information(self, "Успех", f"Сервис {self.service_info.name} добавлен в избранное")
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось добавить в избранное")

class ComparisonDialog(QDialog):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setWindowTitle("Сравнение тарифов")
        self.setGeometry(200, 200, 900, 600)
        
        layout = QVBoxLayout()
        
        self.table = QTableWidget()
        self.table.setColumnCount(len(services) + 1)
        headers = ["Параметр"] + [s.name for s in services]
        self.table.setHorizontalHeaderLabels(headers)
        
        self.populate_table()
        self.table.resizeColumnsToContents()
        self.table.setAlternatingRowColors(True)
        
        layout.addWidget(self.table)
        
        total_label = QLabel(self.calculate_totals())
        total_label.setStyleSheet("font-weight: bold; color: #2c3c4d;")
        layout.addWidget(total_label)
        
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
        all_data = []
        
        all_data.append(("Категория", [s.category for s in self.services]))
        
        for i, service in enumerate(self.services):
            if service.plans:
                cheapest = min(service.plans, key=lambda x: x.price_per_month)
                all_data.append((f"Тариф {i+1} (мин.)", 
                                [""] * i + [f"{cheapest.name}: {cheapest.price_per_month} {cheapest.currency}"] + [""] * (len(self.services) - i - 1)))
                
                expensive = max(service.plans, key=lambda x: x.price_per_month)
                all_data.append((f"Тариф {i+1} (макс.)", 
                                [""] * i + [f"{expensive.name}: {expensive.price_per_month} {expensive.currency}"] + [""] * (len(self.services) - i - 1)))
                
                avg_price = sum(p.price_per_month for p in service.plans) / len(service.plans)
                all_data.append((f"Средняя цена {i+1}", 
                                [""] * i + [f"{avg_price:.2f} {service.plans[0].currency}"] + [""] * (len(self.services) - i - 1)))
        
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
                    headers = ["Параметр"] + [s.name for s in self.services]
                    f.write(";" + ";".join(headers[1:]) + "\n")
                    
                    for row in range(self.table.rowCount()):
                        row_data = []
                        for col in range(self.table.columnCount()):
                            item = self.table.item(row, col)
                            row_data.append(item.text() if item else "")
                        f.write(";".join(row_data) + "\n")
                
                QMessageBox.information(self, "Успех", f"Данные сохранены в {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {e}")

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()