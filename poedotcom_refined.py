import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import json
from datetime import datetime
import re
import os
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
import pickle

# ------------------------
# Poe.com Web Scraper
# ------------------------

# 1. Get Specialty
def get_specialty():
    try:
        url = "https://www.poe.com/about"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p', {'class': 'qtext_para u-ltr u-text-align--start'}, limit=5)
        specialty_texts = [para.text.strip() for para in paragraphs]
        return {"specialty": specialty_texts}
    except requests.exceptions.RequestException as req_err:
        print(f"Request error: {req_err}")
    except Exception as err:
        print(f"An error occurred: {err}")
    # fallback in case of error
    return {"specialty": []}

# 2. Get NSFW Policy
def get_nsfw_policy():
    # URLs for Privacy Policy and Terms of Service
    policy_urls = {
        "privacy_policy": "https://poe.com/privacy",
        "terms_of_service": "https://poe.com/tos"
    }
    
    # NSFW-related keyword categories
    categories = {
        "Advertised": ["explicit content", "NSFW content", "adult content", "nudity"],
        "Allowed but not advertised": ["content moderation", "user responsibility", "user-generated content"],
        "Prohibited": ["prohibited content", "restricted content", "no adult content", "banned"]
    }
    
    # Store results for each document
    policies = {}

    for name, url in policy_urls.items():
        try:
            # Fetch content from URL
            response = requests.get(url, timeout=10)  # Set timeout for request
            response.raise_for_status()  # Raise HTTPError for bad responses
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract text content and normalize
            policy_text = soup.get_text().lower()

            # Determine NSFW policy category
            nsfw_category = "Unknown"
            for category, keywords in categories.items():
                if any(keyword in policy_text for keyword in keywords):
                    nsfw_category = category
                    break

            # Store result
            policies[name] = {
                "url": url,
                "nsfw_policy_category": nsfw_category,
                "summary": "NSFW policy found" if nsfw_category != "Unknown" else "NSFW policy not mentioned"
            }
        except requests.exceptions.RequestException as e:
            # Handle any request errors
            policies[name] = {
                "url": url,
                "nsfw_policy_category": "Error",
                "summary": f"Failed to retrieve or process the document: {e}"
            }
            # continue  # Skip to the next URL
    return {"nsfw_policy": policies}

# ********************************************************************************************************************************
# 3. Get Pricing Options (selenium required to address login roadblock)
class VerificationCodeRetrievalError(Exception):
    """Custom exception raised when verification code cannot be retrieved."""
    def __init__(self, message="Unable to retrieve the verification code after maximum retries."):
        self.message = message
        super().__init__(self.message)

# Define Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# (a) gmail authentication with error handling
def get_gmail_credentials(target_email="[enter default target address here]"):
    """Authenticate Gmail API for a specific target email."""
    try:
        creds = None

        # Force re-authentication by removing the existing token
        if os.path.exists('token.json'):
            os.remove('token.json')

        # OAuth flow for Gmail API
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=8080)

        # Verify authenticated email matches the target email
        service = build('gmail', 'v1', credentials=creds)
        profile = service.users().getProfile(userId='me').execute()
        authenticated_email = profile.get('emailAddress')

        if authenticated_email != target_email:
            raise ValueError(f"Authenticated email ({authenticated_email}) does not match target email ({target_email}).")

        # Save credentials to file
        with open('token.json', 'wb') as token_file:
            pickle.dump(creds, token_file)

        return creds
    except Exception as e:
        print(f"Error during Gmail authentication: {e}")
        return None

# (b) fetch verification code from gmail
def get_verification_code_from_email(target_email="[enter default target address here]"):
    """Fetch the latest verification code from unread emails."""
    try:
        creds = get_gmail_credentials(target_email)
        if not creds:
            return None

        service = build('gmail', 'v1', credentials=creds)
        query = f'to:{target_email} is:unread'
        results = service.users().messages().list(userId='me', q=query).execute()
        messages = results.get('messages', [])

        if not messages:
            print("No unread messages found.")
            return None

        # Extract the verification code from the first unread message
        msg = service.users().messages().get(userId='me', id=messages[0]['id']).execute()
        msg_snippet = msg.get('snippet', '')

        # Match 6-digit verification code
        match = re.search(r'\d{6}', msg_snippet)
        return match.group(0) if match else None
    except Exception as e:
        print(f"Error retrieving verification code: {e}")
        return None

def parse_subscription_details(pricing_soup):
    """Helper function to parse subscription details from a BeautifulSoup object."""

    plans = []
    for plan in pricing_soup.find_all('div', class_='WebSubscriptionTierPlans_tierOption__w24oz'):
        plan_name = plan.find('div', class_='WebSubscriptionTierPlans_title___ChXj').text.strip()
        price = plan.find('div', class_='WebSubscriptionTierPlans_tierPrice__TOXPR').text.strip()
        plans.append({'plan_name': plan_name, 'price': price})
    
    return plans

# (c) fetch pricing information using selenium
def get_pricing_info(email_address, max_retries=10):
    """Automate login and fetch subscription pricing information."""
    driver = webdriver.Safari()
    driver.implicitly_wait(10)

    try:
        driver.get("https://www.poe.com")

        # Step 1: Enter email and proceed
        email_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "textInput_input__9YpqY"))
        )
        email_input.send_keys(email_address)

        go_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".Button_buttonBase__Bv9Vx.Button_primary__6UIn0"))
        )
        go_button.click()

        # Step 2: Wait and retrieve verification code
        verification_code = None
        retries = 0
        while not verification_code and retries < max_retries:
            print(f"Attempt {retries + 1} of {max_retries} to retrieve the verification code...")
            
            verification_code = get_verification_code_from_email()
            
            if not verification_code:
                retries += 1
                time.sleep(5)  # Wait before trying again

        if not verification_code:
            print("Failed to retrieve the verification code after maximum retries.")
            raise VerificationCodeRetrievalError()

        # Step 3: Enter verification code
        verification_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Code']"))
        )
        verification_input.send_keys(verification_code)

        log_in_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".Button_buttonBase__Bv9Vx.Button_primary__6UIn0"))
        )
        log_in_button.click()

        # # close the new screen pop-up
        # close_popup_button = WebDriverWait(driver, 10).until(
        #     EC.element_to_be_clickable((By.CSS_SELECTOR, ".Button_buttonBase__Bv9Vx.Button_flat__dcKQ1.Modal_closeButton__GycnR"))
        #     )
        # close_popup_button.click()

        # Step 4: Navigate to subscription page and extract details
        subscribe_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[text()='Subscribe']"))
        )
        subscribe_button.click()

        pricing_popup = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "WebSubscriptionPaywall_tierContainer__s_5Zw"))
        )
        pricing_html = pricing_popup.get_attribute("outerHTML")

        soup = BeautifulSoup(pricing_html, 'html.parser')
        subscription_title = soup.find('div', class_='WebSubscriptionPaywall_title__hZ9zT')
        subscription_title = subscription_title.text.strip() if subscription_title else "Unavailable"
        features = [li.text.strip() for li in soup.find_all('li', class_='WebSubscriptionPaywall_itemText__Kbotl')]

        # --- yearly subscription details
        yearly_plans = parse_subscription_details(pricing_html=soup)

        monthly_option_checkbox = driver.find_element(By.XPATH, "//label/div[contains(text(),'monthly')]")
        monthly_option_checkbox.click()

        # --- monthly subscription details
        pricing_html = driver.find_element(By.CLASS_NAME, "WebSubscriptionPaywall_tierContainer__s_5Zw").get_attribute("outerHTML")
        monthly_plans = parse_subscription_details(pricing_html=BeautifulSoup(pricing_html, "html.parser"))
        
        plans = {
            "yearly": yearly_plans,
            "monthly": monthly_plans
        }

        return {
            "subscription_title": subscription_title,
            "features": features,
            "plans": plans
        }
    except TimeoutException:
        return {"error": "Operation timed out."}
    except NoSuchElementException:
        return {"error": "Expected element not found on page."}
    except Exception as e:
        return {"error": str(e)}
    finally:
        driver.quit()
# ********************************************************************************************************************************

# 4. Get Useful Links
def get_useful_links():
    links = {
        "about": "https://poe.com/about",
        "privacy_policy": "https://poe.com/privacy",
        "terms_of_service": "https://poe.com/tos",
        "faqs": "https://help.poe.com/hc/en-us/articles/19944206309524-Poe-FAQs",
        "poe creator monitisation faqs": "https://help.poe.com/hc/en-us/articles/21921312368020-Poe-Creator-Monetization-FAQs",
        "updating stripe information": "https://help.poe.com/hc/en-us/articles/31861081903508-How-do-I-update-my-personal-information-on-Stripe",
        "common creator monetization program participant tax questions": "https://help.poe.com/hc/en-us/articles/31861685533204-Common-Creator-Monetization-Program-Participant-Tax-Questions",
        "poe subscriptions faqs":"https://help.poe.com/hc/en-us/articles/19945140063636-Poe-Subscriptions-FAQs"
    }
    return {"useful_links": links}

# 5. Get Server Status (using the provided form)
def initialise_webdriver(browser="safari"):
    """
    Initializes the WebDriver based on the specified browser.
    
    Args:
        browser (str): Browser to use ("safari", "chrome", or "firefox").
        
    Returns:
        WebDriver: A Selenium WebDriver instance for the specified browser.
        
    Raises:
        ValueError: If an unsupported browser is specified.
        Exception: For any WebDriver initialization errors.
    """
    try:
        if browser.lower() == "chrome":
            return webdriver.Chrome()
        elif browser.lower() == "firefox":
            return webdriver.Firefox()
        elif browser.lower() == "safari":
            return webdriver.Safari()
        else:
            raise ValueError(f"Unsupported browser: {browser}")
    except Exception as e:
        raise Exception(f"Failed to initialise WebDriver: {str(e)}")

def get_server_status(url="<https://www.poe.com>", browser="safari", wait_time=10):
    """
    Check server status of a given URL using isitdownrightnow.com.

    Args:
        url (str): URL to check.
        browser (str): Browser to use for Selenium (e.g., "safari", "chrome", "firefox").
        wait_time (int): Maximum time to wait for page elements to load (in seconds).

    Returns:
        dict: A dictionary containing the server status, response time, and other metadata.
    """
    try:
		# Initialize the WebDriver
        driver = initialise_webdriver(browser)
		    
        # Record timestamps
        first_checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Open the server status website
        status_url = "<https://www.isitdownrightnow.com/downorjustme.php>"
        driver.get(status_url)

        # Locate the input field and enter the URL
        input_field = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.NAME, "url"))
        )
        input_field.clear()
        input_field.send_keys(url)
        submit_time_start = time.time()  # Start timing the response
        input_field.send_keys(Keys.RETURN)  # Submit the form

        # Wait for the server status to appear
        status_message = WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.CLASS_NAME, "statusup"))
        ).text.strip()

        response_time = round(time.time() - submit_time_start, 3)  # Calculate response time
        last_checked = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Record end time

        return {
            "server_status": {
                "url": url,
                "first_checked": first_checked,
                "status": f"{status_message} ({status_url})",
                "response_time": response_time,
                "error": None,
                "last_checked": last_checked,
            }
        }
    except TimeoutException:
        return {
            "server_status": {
                "url": url,
                "first_checked": first_checked,
                "status": "Timeout while fetching status",
                "response_time": 0,
                "error": "TimeoutException",
                "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        }
    except NoSuchElementException:
        return {
            "server_status": {
                "url": url,
                "first_checked": first_checked,
                "status": "Required element not found",
                "response_time": 0,
                "error": "NoSuchElementException",
                "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        }
    except Exception as e:
        return {
            "server_status": {
                "url": url,
                "first_checked": first_checked,
                "status": "Error",
                "response_time": 0,
                "error": str(e),
                "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        }
    finally:
        driver.quit()

# 6. Get Language Support
def get_language_support(url="https://help.poe.com/hc/en-us/articles/19944206309524-Poe-FAQs", 
                         header_id="h_01HCFXCW5C9EFV5947QPQAQXDP", 
                         browser="safari", 
                         wait_time=10):
    """
    Scrapes the language support information from a given URL.
    
    Args:
        url (str): The URL to scrape.
        header_id (str): The ID of the header to locate the relevant section.
        browser (str): Browser to use for Selenium ("safari", "chrome", "firefox").
        wait_time (int): Maximum wait time for page elements (in seconds).
    
    Returns:
        dict: A dictionary containing the language support information and metadata.
    """
    try:
        # Initialize the WebDriver using the new helper function
        driver = initialise_webdriver(browser)
        
        driver.get(url)  # Open the target URL
        
        # Wait until the page loads completely
        WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.CLASS_NAME, "article-body"))
        )
        
        # Parse the page source using BeautifulSoup
        soup = BeautifulSoup(driver.page_source, "html.parser")
        article_body = soup.find("div", {"class": "article-body"})
        
        if not article_body:
            raise ValueError("Article body not found.")
        
        header = article_body.find("h2", {"id": header_id})
        if not header:
            raise ValueError(f"Header with ID '{header_id}' not found.")
        
        next_element = header.find_next("p")
        if not next_element:
            raise ValueError("No <p> tag found after the specified header.")
        
        # Extract and clean the language support text
        language_support = next_element.text.strip()
        metadata = {
            "languages_supported": language_support,
            "source_url": url,
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": None
        }
        
        return metadata

    except (TimeoutException, NoSuchElementException) as e:
        return {
            "languages_supported": None,
            "source_url": url,
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e)
        }
    except Exception as e:
        return {
            "languages_supported": None,
            "source_url": url,
            "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e)
        }
    finally:
        driver.quit()  # Ensure the browser is closed properly

# ------------------------
# 7. Save Data to JSON
# ------------------------

def save_to_json(data, filename="poe_dot_com_data.json"):
    with open(f"./{filename}", "w") as json_file:
        json.dump(data, json_file, indent=4)

# ------------------------
# Main Program
# ------------------------

def main():
    data = {}

    data.update(get_specialty())
    # # print(data)

    data.update(get_nsfw_policy())
    # # print(data["nsfw_policy"])

    data.update(get_pricing_info("[enter chosen email address here]"))
    # print(data["pricing"])

    data.update(get_useful_links())
    # # print(data["useful_links"])

    data.update(get_server_status())
    # # print(data["server_status"])

    data.update(get_language_support())
    # # print(data["languages_supported"])
    
    save_to_json(data)

if __name__ == "__main__":
    main()