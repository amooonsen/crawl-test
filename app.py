import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time
import unicodedata
import io
import csv
from PIL import Image, ImageDraw
import logging
import os
import hashlib
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CACHE_DIR = "screenshot_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

class SiteIACrawler:
    def __init__(self):
        self.base_url = None
        self.gnb_links = []  # 계층적 구조: [{'text': '1뎁스', 'url': 'url', 'children': [...]}]
        self.side_links = []
        self.footer_links = []
        self.other_links = []

    def setup_driver(self):
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
            
            # Streamlit Cloud 환경에 맞게 설정
            options.binary_location = "/usr/bin/chromium"
            service = Service("/usr/lib/chromium/chromedriver")
            
            driver = webdriver.Chrome(service=service, options=options)
            return driver
        except Exception as e:
            logger.error(f"드라이버 설정 실패: {str(e)}")
            raise

    def find_gnb_element(self, soup):
        nav_patterns = {
            'semantic_tags': ['nav', 'header'],
            'common_classes': ['gnb', 'nav', 'navigation', 'menu', 'main-menu', 'top-menu', 'topmenu', 'global-nav', 'util-menu', 'user-menu', 'header-menu'],
            'common_ids': ['gnb', 'nav', 'navigation', 'menu', 'top-menu', 'topmenu', 'global-nav', 'util-menu', 'user-menu', 'header-menu']
        }
        potential_navs = []
        for tag in nav_patterns['semantic_tags']:
            elements = soup.find_all(tag)
            potential_navs.extend(elements)
        for class_name in nav_patterns['common_classes']:
            elements = soup.find_all(class_=re.compile(f".*{class_name}.*", re.I))
            potential_navs.extend(elements)
        for id_name in nav_patterns['common_ids']:
            element = soup.find(id=re.compile(f".*{id_name}.*", re.I))
            if element:
                potential_navs.append(element)

        if not potential_navs:
            return None

        scored_elements = []
        for element in potential_navs:
            score = 0
            if element.name == 'nav':
                score += 30
            if element.find_parent('header'):
                score += 20
            links = element.find_all('a')
            score += min(len(links) * 2, 20)
            if element.find(['ul', 'ol']):
                score += 15
            if any('login' in link.get_text().lower() or 'logout' in link.get_text().lower() or 'join' in link.get_text().lower() or 'mypage' in link.get_text().lower() for link in links):
                score += 10  # 로그인, 회원가입 등이 포함된 메뉴에 가산점
            scored_elements.append((element, score))
        
        return max(scored_elements, key=lambda x: x[1])[0] if scored_elements else None

    def find_side_element(self, soup):
        side_patterns = {
            'semantic_tags': ['aside'],
            'common_classes': ['sidebar', 'side', 'side-menu', 'side-nav'],
            'common_ids': ['sidebar', 'side', 'side-menu']
        }
        potential_sides = []
        for tag in side_patterns['semantic_tags']:
            elements = soup.find_all(tag)
            potential_sides.extend(elements)
        for class_name in side_patterns['common_classes']:
            elements = soup.find_all(class_=re.compile(f".*{class_name}.*", re.I))
            potential_sides.extend(elements)
        for id_name in side_patterns['common_ids']:
            element = soup.find(id=re.compile(f".*{id_name}.*", re.I))
            if element:
                potential_sides.append(element)

        if not potential_sides:
            return None

        scored_elements = []
        for element in potential_sides:
            score = 0
            if element.name == 'aside':
                score += 20
            links = element.find_all('a')
            score += min(len(links) * 2, 20)
            if element.find(['ul', 'ol']):
                score += 15
            scored_elements.append((element, score))
        
        return max(scored_elements, key=lambda x: x[1])[0] if scored_elements else None

    def find_footer_element(self, soup):
        footer_selectors = [
            'footer', '#footer', '.footer',
            '[class*="footer"]', '[class*="Footer"]',
            '.bottom', '#bottom', '.site-bottom'
        ]
        footer_candidates = []
        for selector in footer_selectors:
            elements = soup.select(selector)
            footer_candidates.extend(elements)

        if not footer_candidates:
            all_elements = soup.find_all(['div', 'section'])
            for element in all_elements:
                if any(keyword in element.get_text().lower() for keyword in ['이용약관', '개인정보', '사이트맵', '회사소개']):
                    footer_candidates.append(element)

        if not footer_candidates:
            return None

        scored_elements = []
        for element in footer_candidates:
            score = 0
            links = element.find_all('a')
            score += len(links) * 2
            if element.name == 'footer':
                score += 20
            if any(cls and 'footer' in cls.lower() for cls in element.get('class', [])):
                score += 10
            scored_elements.append((element, score))
        
        return max(scored_elements, key=lambda x: x[1])[0] if scored_elements else None

    def extract_links(self, soup, element=None, section="Other", depth=1):
        links = []
        seen = set()

        def process_link(link):
            href = link.get('href', '#')
            text = link.get_text(strip=True)
            if text:
                text = unicodedata.normalize('NFKC', text)
                text = re.sub(r'[\xa0\u200b\u200c\u200d]', '', text)
                text = ' '.join(text.split())
            if not text or href == '#' or href.startswith('javascript:'):
                return None
            url = urljoin(self.base_url, href)
            return (text, url)

        target = element if element else soup
        # 모든 <a> 태그를 재귀적으로 탐색
        for li in target.find_all('li', recursive=True):  # recursive=True로 모든 하위 뎁스 탐색
            a_tag = li.find('a')
            if a_tag:
                link_info = process_link(a_tag)
                if link_info and link_info[1] not in seen:  # URL 기준으로 중복 제거
                    seen.add(link_info[1])
                    children = []
                    submenu = li.find(['ul', 'div'], recursive=False)
                    if submenu:
                        for sub_li in submenu.find_all('li', recursive=True):
                            sub_a = sub_li.find('a')
                            if sub_a:
                                sub_link_info = process_link(sub_a)
                                if sub_link_info and sub_link_info[1] not in seen:
                                    seen.add(sub_link_info[1])
                                    children.append({
                                        'text': sub_link_info[0],
                                        'url': sub_link_info[1],
                                        'section': section,
                                        'depth': depth + 1
                                    })
                    links.append({
                        'text': link_info[0],
                        'url': link_info[1],
                        'section': section,
                        'depth': depth,
                        'children': children
                    })
            else:
                submenu = li.find(['ul', 'div'], recursive=False)
                if submenu:
                    links.extend(self.extract_links(soup, submenu, section, depth + 1))

        # <li> 구조가 없는 경우 직접 <a> 태그 탐색 (예: 로그인, 회원가입 버튼)
        for a in target.find_all('a', recursive=True):  # recursive=True로 모든 하위 뎁스 탐색
            link_info = process_link(a)
            if link_info and link_info[1] not in seen:
                seen.add(link_info[1])
                links.append({
                    'text': link_info[0],
                    'url': link_info[1],
                    'section': section,
                    'depth': depth,
                    'children': []
                })
        return links

    def crawl(self, url):
        self.base_url = url
        try:
            # 먼저 Selenium으로 시도
            try:
                driver = self.setup_driver()
                logger.info(f"크롤링 시작: {url}")
                driver.get(url)
                WebDriverWait(driver, 15).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )

                # 페이지 스크롤 (동적 콘텐츠 로드 유도)
                for _ in range(3):
                    last_height = driver.execute_script("return document.body.scrollHeight")
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(1)
                    new_height = driver.execute_script("return document.body.scrollHeight")
                    if new_height == last_height:
                        break

                # 최종 페이지 소스 가져오기
                soup = BeautifulSoup(driver.page_source, "html.parser")
                driver.quit()
            except Exception as e:
                logger.warning(f"Selenium 크롤링 실패, requests로 대체: {str(e)}")
                # Selenium 실패 시 requests 사용
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                }
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")

            # GNB 및 Top Menu 추출
            gnb_element = self.find_gnb_element(soup)
            self.gnb_links = self.extract_links(soup, gnb_element, "GNB", depth=1) if gnb_element else []
            self.side_links = self.extract_links(soup, self.find_side_element(soup), "Side Menu") if self.find_side_element(soup) else []
            self.footer_links = self.extract_links(soup, self.find_footer_element(soup), "Footer") if self.find_footer_element(soup) else []

            all_links = self.extract_links(soup, section="Other")
            seen_urls = set()
            for link in self.gnb_links + self.side_links + self.footer_links:
                seen_urls.add(link['url'])
                for child in link.get('children', []):
                    seen_urls.add(child['url'])
            self.other_links = [link for link in all_links if link['url'] not in seen_urls]

            logger.info(f"크롤링 완료: {url}")
            return True
        except Exception as e:
            logger.error(f"크롤링 중 오류 발생: {str(e)}")
            return str(e)

    def generate_txt(self):
        output = io.StringIO()
        output.write(f"사이트 IA 구조도 ({self.base_url})\n")
        output.write("=" * 50 + "\n\n")

        output.write("📌 GNB 메뉴\n")
        if self.gnb_links:
            for link in self.gnb_links:
                output.write(f"├── {link['text']} - {link['url']}\n")
                for child in link.get('children', []):
                    output.write(f"│   ├── {child['text']} - {child['url']}\n")
        else:
            output.write("├── GNB 데이터 없음\n")
        output.write("\n")

        output.write("📌 Side Menu\n")
        if self.side_links:
            for link in self.side_links:
                output.write(f"├── {link['text']} - {link['url']}\n")
                for child in link.get('children', []):
                    output.write(f"│   ├── {child['text']} - {child['url']}\n")
        else:
            output.write("├── Side Menu 데이터 없음\n")
        output.write("\n")

        output.write("📌 Footer 메뉴\n")
        if self.footer_links:
            for link in self.footer_links:
                output.write(f"├── {link['text']} - {link['url']}\n")
                for child in link.get('children', []):
                    output.write(f"│   ├── {child['text']} - {child['url']}\n")
        else:
            output.write("├── Footer 데이터 없음\n")
        output.write("\n")

        output.write("📌 기타 링크\n")
        if self.other_links:
            for link in self.other_links:
                output.write(f"├── {link['text']} - {link['url']}\n")
                for child in link.get('children', []):
                    output.write(f"│   ├── {child['text']} - {child['url']}\n")
        else:
            output.write("├── 기타 데이터 없음\n")

        return output.getvalue()

    def generate_csv(self):
        output = io.StringIO()
        output.write('\ufeff')
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(['섹션', '텍스트', 'URL', '뎁스'])

        def write_links(links, section):
            for link in links:
                writer.writerow([section, link['text'], link['url'], link['depth']])
                for child in link.get('children', []):
                    writer.writerow([section, child['text'], child['url'], child['depth']])

        write_links(self.gnb_links, 'GNB')
        write_links(self.side_links, 'Side Menu')
        write_links(self.footer_links, 'Footer')
        write_links(self.other_links, 'Other')

        return output.getvalue()

    def generate_md(self):
        output = io.StringIO()
        output.write(f"# 사이트 IA 구조도 ({self.base_url})\n\n")

        output.write("## 📌 GNB 메뉴\n\n")
        if self.gnb_links:
            for link in self.gnb_links:
                output.write(f"- [{link['text']}]({link['url']})\n")
                for child in link.get('children', []):
                    output.write(f"  - [{child['text']}]({child['url']})\n")
        else:
            output.write("GNB 데이터 없음\n\n")

        output.write("\n## 📌 Side Menu\n\n")
        if self.side_links:
            for link in self.side_links:
                output.write(f"- [{link['text']}]({link['url']})\n")
                for child in link.get('children', []):
                    output.write(f"  - [{child['text']}]({child['url']})\n")
        else:
            output.write("Side Menu 데이터 없음\n\n")

        output.write("\n## 📌 Footer 메뉴\n\n")
        if self.footer_links:
            for link in self.footer_links:
                output.write(f"- [{link['text']}]({link['url']})\n")
                for child in link.get('children', []):
                    output.write(f"  - [{child['text']}]({child['url']})\n")
        else:
            output.write("Footer 데이터 없음\n\n")

        output.write("\n## 📌 기타 링크\n\n")
        if self.other_links:
            for link in self.other_links:
                output.write(f"- [{link['text']}]({link['url']})\n")
                for child in link.get('children', []):
                    output.write(f"  - [{child['text']}]({child['url']})\n")
        else:
            output.write("기타 데이터 없음\n")

        return output.getvalue()

    def get_cache_path(self, url, width):
        hash_key = hashlib.md5(f"{url}_{width}".encode()).hexdigest()
        return os.path.join(CACHE_DIR, f"{hash_key}.png")

    def handle_popup(self, driver):
        try:
            close_button_selectors = [
                'button.close',
                'a.close',
                '[class*="close"]',
                '[id*="close"]',
                'button[aria-label="Close"]',
                'button[aria-label="close"]',
                'div.close-btn',
                '.btn-close',
                '.modal-close'
            ]

            for selector in close_button_selectors:
                try:
                    close_button = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    close_button.click()
                    logger.info("팝업 닫기 버튼 클릭 성공")
                    time.sleep(1)
                    return True
                except Exception as e:
                    continue

            logger.info("닫기 버튼을 찾지 못해 팝업 요소 숨김 처리")
            driver.execute_script("""
                var popups = document.querySelectorAll('.modal, .popup, .overlay, [id*="popup"], [class*="popup"], [id*="modal"], [class*="modal"], [id*="overlay"], [class*="overlay"]');
                popups.forEach(function(el) {
                    el.style.display = 'none';
                });
            """)
            return True
        except Exception as e:
            logger.warning(f"팝업 처리 중 오류: {str(e)}")
            return False

    def capture_screenshot(self, url, width):
        cache_path = self.get_cache_path(url, width)
        
        if os.path.exists(cache_path):
            logger.info(f"캐시에서 스크린샷 로드: {url} (width: {width})")
            with open(cache_path, 'rb') as f:
                return f.read()

        try:
            driver = self.setup_driver()
            try:
                logger.info(f"스크린샷 캡처 시작: {url} (width: {width})")
                driver.set_window_size(width, 1080)
                driver.get(url)

                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )

                self.handle_popup(driver)

                driver.execute_script("document.body.style.overflow = 'hidden';")

                for _ in range(3):
                    last_height = driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);")
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(1)
                    new_height = driver.execute_script("return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);")
                    if new_height == last_height:
                        break

                driver.set_window_size(width, last_height)
                time.sleep(1)

                screenshot = driver.get_screenshot_as_png()
                logger.info(f"스크린샷 캡처 완료: {url}")

                with open(cache_path, 'wb') as f:
                    f.write(screenshot)

                return screenshot
            finally:
                driver.quit()
        except Exception as e:
            logger.error(f"스크린샷 캡처 실패: {url} - {str(e)}")
            # 실패 시 대체 이미지 생성
            img = Image.new('RGB', (width, 400), color = (240, 240, 240))
            d = ImageDraw.Draw(img)
            d.text((20, 20), f"스크린샷 캡처 실패: {url}", fill=(0, 0, 0))
            d.text((20, 50), "Streamlit Cloud 환경에서 스크린샷 기능이 제한됩니다.", fill=(0, 0, 0))
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='PNG')
            img_bytes.seek(0)
            
            # 캐시에 저장
            with open(cache_path, 'wb') as f:
                f.write(img_bytes.getvalue())
                
            return img_bytes.getvalue()

# Streamlit UI
st.set_page_config(page_title="사이트 IA 구조도 크롤러", layout="wide")
st.title("사이트 IA 구조도 크롤러")

url = st.text_input("크롤링할 URL을 입력하세요")

if st.button("크롤링 시작"):
    if not url:
        st.error("URL을 입력해주세요.")
    else:
        if not urlparse(url).scheme:
            url = "https://" + url
        
        with st.spinner("크롤링 중..."):
            crawler = SiteIACrawler()
            result = crawler.crawl(url)
            
            if result is True:
                st.success("크롤링 완료!")
                
                # GNB 메뉴 표시
                st.header("📌 GNB 메뉴")
                if crawler.gnb_links:
                    for link in crawler.gnb_links:
                        st.markdown(f"- [{link['text']}]({link['url']})")
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button(f"PC 스크린샷 - {link['text']}", key=f"pc_{link['url']}"):
                                screenshot = crawler.capture_screenshot(link['url'], 1920)
                                st.image(screenshot)
                        with col2:
                            if st.button(f"모바일 스크린샷 - {link['text']}", key=f"mo_{link['url']}"):
                                screenshot = crawler.capture_screenshot(link['url'], 360)
                                st.image(screenshot)
                        
                        if link.get('children'):
                            for child in link['children']:
                                st.markdown(f"  - [{child['text']}]({child['url']})")
                else:
                    st.info("GNB 데이터 없음")
                
                # Side Menu 표시
                st.header("📌 Side Menu")
                if crawler.side_links:
                    for link in crawler.side_links:
                        st.markdown(f"- [{link['text']}]({link['url']})")
                        if link.get('children'):
                            for child in link['children']:
                                st.markdown(f"  - [{child['text']}]({child['url']})")
                else:
                    st.info("Side Menu 데이터 없음")
                
                # Footer 메뉴 표시
                st.header("📌 Footer 메뉴")
                if crawler.footer_links:
                    for link in crawler.footer_links:
                        st.markdown(f"- [{link['text']}]({link['url']})")
                        if link.get('children'):
                            for child in link['children']:
                                st.markdown(f"  - [{child['text']}]({child['url']})")
                else:
                    st.info("Footer 데이터 없음")
                
                # 기타 링크 표시
                st.header("📌 기타 링크")
                if crawler.other_links:
                    for link in crawler.other_links:
                        st.markdown(f"- [{link['text']}]({link['url']})")
                        if link.get('children'):
                            for child in link['children']:
                                st.markdown(f"  - [{child['text']}]({child['url']})")
                else:
                    st.info("기타 데이터 없음")
                
                # 다운로드 버튼
                col1, col2, col3 = st.columns(3)
                with col1:
                    txt_content = crawler.generate_txt()
                    st.download_button(
                        label="TXT 다운로드",
                        data=txt_content,
                        file_name="site_ia.txt",
                        mime="text/plain"
                    )
                with col2:
                    csv_content = crawler.generate_csv()
                    st.download_button(
                        label="CSV 다운로드",
                        data=csv_content,
                        file_name="site_ia.csv",
                        mime="text/csv"
                    )
                with col3:
                    md_content = crawler.generate_md()
                    st.download_button(
                        label="MD 다운로드",
                        data=md_content,
                        file_name="site_ia.md",
                        mime="text/markdown"
                    )
            else:
                st.error(f"크롤링 실패: {result}")