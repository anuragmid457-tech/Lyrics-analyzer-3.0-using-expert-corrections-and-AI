#!/usr/bin/env python3
"""
Rabindra Sangeet Dataset Creator
Scrapes lyrics from geetabitan.com and organizes by emotions
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
from typing import List, Dict, Tuple
import time
import csv
from urllib.parse import urljoin
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RabindraSangeetScraper:
    """
    Scrapes Rabindra Sangeet songs from geetabitan.com
    """
    
    def __init__(self):
        self.base_url = "https://www.geetabitan.com"
        self.songs_data = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Emotion mapping based on Gitabitan categories
        self.emotion_mapping = {
            'Puja': 'Devotional',  # Worship, spiritual
            'Prem': 'Love',  # Love, romance, longing
            'Prakriti': 'Nature',  # Seasonal, nature
            'Swadesh': 'Patriotic',  # Patriotic, nationalist
            'Aanushthanik': 'Occasional',  # Occasion-specific
            'Bichitro': 'Miscellaneous',  # Various emotions
            'Nrityonatya': 'Dance-Drama'  # Dance dramas
        }
    
    def get_song_list_urls(self) -> List[str]:
        """
        Get URLs for song lists by alphabet
        Returns list of URLs for songs starting with A-Z
        """
        alphabets = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        urls = []
        
        for letter in alphabets:
            url = f"{self.base_url}/lyrics/{letter}/song-list.html"
            urls.append(url)
        
        return urls
    
    def fetch_songs_for_letter(self, letter: str) -> List[Dict]:
        """
        Fetch all songs starting with a particular letter
        """
        url = f"{self.base_url}/lyrics/{letter}/song-list.html"
        songs = []
        
        try:
            logger.info(f"Fetching songs starting with {letter}...")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Find song entries (this may need adjustment based on actual HTML structure)
                song_entries = soup.find_all('a', class_='song-link')
                
                if not song_entries:
                    # Try alternative selectors
                    song_entries = soup.find_all('a', href=lambda x: x and '/lyrics/' in x)
                
                for entry in song_entries:
                    song_name = entry.get_text().strip()
                    song_url = urljoin(self.base_url, entry.get('href', ''))
                    
                    if song_name and song_url:
                        songs.append({
                            'title': song_name,
                            'url': song_url,
                            'letter': letter
                        })
                        logger.debug(f"Found song: {song_name}")
            
            time.sleep(1)  # Be respectful to server
            
        except Exception as e:
            logger.error(f"Error fetching songs for letter {letter}: {str(e)}")
        
        return songs
    
    def fetch_song_details(self, song_url: str) -> Dict:
        """
        Fetch detailed information about a song including lyrics
        """
        song_details = {
            'title': '',
            'lyrics': '',
            'parjaay': '',
            'raag': '',
            'taal': '',
            'background': '',
            'emotion': ''
        }
        
        try:
            response = requests.get(song_url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Extract title
                title_elem = soup.find('h1')
                if title_elem:
                    song_details['title'] = title_elem.get_text().strip()
                
                # Extract lyrics (usually in a specific container)
                lyrics_containers = soup.find_all(['pre', 'div'], class_=['lyrics', 'song-lyrics'])
                if lyrics_containers:
                    song_details['lyrics'] = '\n'.join([
                        container.get_text().strip() 
                        for container in lyrics_containers
                    ])
                
                # Extract metadata
                metadata_fields = {
                    'Parjaay': 'parjaay',
                    'Raag': 'raag',
                    'Taal': 'taal',
                    'Background': 'background'
                }
                
                for key, field in metadata_fields.items():
                    elem = soup.find(text=key)
                    if elem:
                        value_elem = elem.find_next('span')
                        if value_elem:
                            song_details[field] = value_elem.get_text().strip()
                
                time.sleep(0.5)  # Be respectful
            
        except Exception as e:
            logger.error(f"Error fetching song details from {song_url}: {str(e)}")
        
        return song_details
    
    def classify_emotion(self, song_data: Dict) -> str:
        """
        Classify emotion based on various signals
        """
        emotion = 'Miscellaneous'
        
        # Use parjaay if available
        if song_data.get('parjaay'):
            parjaay = song_data['parjaay'].lower()
            
            if 'vasant' in parjaay or 'spring' in parjaay:
                emotion = 'Joy/Spring'
            elif 'grisma' in parjaay or 'summer' in parjaay:
                emotion = 'Yearning'
            elif 'varsha' in parjaay or 'monsoon' in parjaay:
                emotion = 'Longing'
            elif 'sharad' in parjaay or 'autumn' in parjaay:
                emotion = 'Melancholy'
            elif 'hemanta' in parjaay or 'winter' in parjaay:
                emotion = 'Separation/Sadness'
        
        # Analyze lyrics for emotion keywords
        lyrics = song_data.get('lyrics', '').lower()
        
        emotion_keywords = {
            'Love/Romance': ['prem', 'bhalo', 'mon', 'hridoy', 'valobasi', 'chao'],
            'Sadness': ['dukkho', 'ashru', 'kando', 'byatha', 'peran'],
            'Longing': ['tumi', 'tomar', 'jabe', 'jodio', 'paboni', 'maane'],
            'Devotion': ['devi', 'prabhu', 'bhagaban', 'ishwar', 'puja', 'upasana'],
            'Patriotic': ['bangla', 'desh', 'jati', 'khani', 'swadesh'],
            'Joy': ['ananda', 'khusi', 'ujjwal', 'alok', 'nritya'],
            'Desperation': ['jeno', 'keno', 'kothay', 'kichu', 'paboni', 'ache']
        }
        
        for emo, keywords in emotion_keywords.items():
            if any(keyword in lyrics for keyword in keywords):
                emotion = emo
                break
        
        return emotion
    
    def scrape_all_songs(self) -> pd.DataFrame:
        """
        Main method to scrape all songs
        """
        all_songs_data = []
        alphabets = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        
        logger.info("Starting Rabindra Sangeet collection...")
        logger.info(f"Total letters to process: {len(alphabets)}")
        
        for letter in alphabets:
            logger.info(f"\n{'='*50}")
            logger.info(f"Processing songs starting with: {letter}")
            logger.info(f"{'='*50}")
            
            song_list = self.fetch_songs_for_letter(letter)
            logger.info(f"Found {len(song_list)} songs starting with {letter}")
            
            for song in song_list:
                try:
                    # Fetch detailed information
                    details = self.fetch_song_details(song['url'])
                    
                    if details['title'] and details['lyrics']:
                        # Classify emotion
                        emotion = self.classify_emotion(details)
                        details['emotion'] = emotion
                        
                        all_songs_data.append(details)
                        logger.info(f"✓ Processed: {details['title']} - {emotion}")
                    
                except Exception as e:
                    logger.error(f"Error processing song: {str(e)}")
        
        # Create DataFrame
        df = pd.DataFrame(all_songs_data)
        return df
    
    def save_dataset(self, df: pd.DataFrame, filename: str = 'rabindra_sangeet_dataset.csv'):
        """
        Save dataset to CSV
        """
        try:
            df.to_csv(filename, index=False, encoding='utf-8')
            logger.info(f"\n{'='*50}")
            logger.info(f"Dataset saved to: {filename}")
            logger.info(f"Total songs collected: {len(df)}")
            logger.info(f"Columns: {list(df.columns)}")
            logger.info(f"{'='*50}")
            
            # Print emotion statistics
            if 'emotion' in df.columns:
                logger.info("\nEmotion Distribution:")
                logger.info(df['emotion'].value_counts())
            
        except Exception as e:
            logger.error(f"Error saving dataset: {str(e)}")


# Alternative: Manual Dataset Creation Template
def create_manual_dataset_template(filename: str = 'rabindra_sangeet_template.csv'):
    """
    Creates a template CSV for manual data entry
    """
    template_data = {
        'song_id': [f'RS_{i:04d}' for i in range(1, 11)],
        'song_title': ['[Song Title]'] * 10,
        'artist': ['Rabindranath Tagore'] * 10,
        'lyrics': ['[Full lyrics in Bengali]'] * 10,
        'emotion_primary': ['Love/Sadness/Joy/Devotion/Patriotic/Longing/Desperation/Heartbreak'] * 10,
        'emotion_secondary': [''] * 10,
        'parjaay': ['Vasant/Grisma/Varsha/Sharad/Hemanta'] * 10,
        'raag': ['[Musical Raag]'] * 10,
        'taal': ['[Rhythmic Cycle]'] * 10,
        'year_composed': [''] * 10,
        'background': ['[Song background/meaning]'] * 10,
        'lyrics_english': ['[English translation if available]'] * 10,
        'source': ['geetabitan.com'] * 10
    }
    
    df = pd.DataFrame(template_data)
    df.to_csv(filename, index=False, encoding='utf-8')
    logger.info(f"Template created: {filename}")
    logger.info(f"Please fill in the data manually and add more rows as needed")


# Emotion Classification Rules Based on Research
class EmotionClassifier:
    """
    Advanced emotion classification using text analysis
    """
    
    def __init__(self):
        # Comprehensive emotion keywords in Bengali
        self.emotion_lexicon = {
            'Love': {
                'keywords': ['ভালোবাসি', 'প্রেম', 'মন', 'হৃদয়', 'তুমি', 'তোমার', 'চাও', 'ভালো'],
                'intensity': 'high'
            },
            'Heartbreak': {
                'keywords': ['আঘাত', 'বেদনা', 'হারিয়ে', 'বিরহ', 'কষ্ট', 'ব্যথা', 'শোক'],
                'intensity': 'high'
            },
            'Longing': {
                'keywords': ['তুমি', 'খুঁজি', 'মিলন', 'অপেক্ষা', 'পাব না', 'জেনে', 'জানি'],
                'intensity': 'high'
            },
            'Sadness': {
                'keywords': ['দুঃখ', 'কাঁদি', 'অশ্রু', 'মৃত্যু', 'বিষাদ', 'চিন্তা', 'যন্ত্রণা'],
                'intensity': 'high'
            },
            'Joy': {
                'keywords': ['আনন্দ', 'খুশি', 'হাসি', 'উজ্জ্বল', 'আলো', 'নৃত্য', 'গান'],
                'intensity': 'medium'
            },
            'Devotion': {
                'keywords': ['দেবী', 'প্রভু', 'ভগবান', 'ঈশ্বর', 'পূজা', 'নিবেদন', 'ধর্ম'],
                'intensity': 'high'
            },
            'Patriotism': {
                'keywords': ['বাংলা', 'দেশ', 'জাতি', 'স্বাধীন', 'খানি', 'বাঙ্গালী'],
                'intensity': 'high'
            },
            'Desperation': {
                'keywords': ['কেন', 'কোথা', 'কোথায়', 'পাবো না', 'জানি', 'বুঝি না'],
                'intensity': 'high'
            },
            'Obsession': {
                'keywords': ['শুধু', 'শুধুমাত্র', 'চিন্তা', 'মাতাল', 'পাগল', 'সবসময়'],
                'intensity': 'high'
            }
        }
    
    def classify(self, text: str) -> Tuple[str, float]:
        """
        Classify emotion from Bengali text
        Returns: (emotion, confidence_score)
        """
        text_lower = text.lower()
        emotion_scores = {}
        
        for emotion, data in self.emotion_lexicon.items():
            score = sum(1 for keyword in data['keywords'] if keyword in text_lower)
            emotion_scores[emotion] = score
        
        if max(emotion_scores.values()) > 0:
            best_emotion = max(emotion_scores, key=emotion_scores.get)
            confidence = emotion_scores[best_emotion] / len(text.split())
            return best_emotion, min(confidence, 1.0)
        
        return 'Miscellaneous', 0.0


# Main execution
if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║     Rabindra Sangeet Dataset Creator & Emotion Classifier     ║
    ║                                                               ║
    ║  This script helps you collect Rabindra Sangeet lyrics        ║
    ║  and classify them by emotions                                ║
    ╚═══════════════════════════════════════════════════════════════╝
    """)
    
    print("\nOptions:")
    print("1. Scrape from geetabitan.com (requires BeautifulSoup, requests)")
    print("2. Create manual entry template")
    print("3. View emotion classification rules")
    
    choice = input("\nEnter your choice (1-3): ").strip()
    
    if choice == '1':
        print("\n⚠️  Note: Web scraping may take time (2,232 songs)")
        confirm = input("Continue? (yes/no): ").strip().lower()
        
        if confirm == 'yes':
            scraper = RabindraSangeetScraper()
            df = scraper.scrape_all_songs()
            scraper.save_dataset(df)
    
    elif choice == '2':
        create_manual_dataset_template()
        print("\n✓ Template file created!")
        print("  Edit it and add Rabindra Sangeet songs with emotions")
    
    elif choice == '3':
        classifier = EmotionClassifier()
        print("\nEmotion Keywords (Bengali):")
        for emotion, data in classifier.emotion_lexicon.items():
            print(f"\n{emotion}:")
            print(f"  Keywords: {', '.join(data['keywords'][:5])}...")
    
    print("\n" + "="*60)
    print("Dataset creation complete!")
    print("="*60)