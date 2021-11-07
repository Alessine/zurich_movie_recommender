from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options as FirefoxOptions
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from datetime import date
import re
import json


def is_time_format(input):
    try:
        time.strptime(input, '%H:%M')
        return True
    except ValueError:
        return False


def scrape_cineman(cities=("Zürich")):
    """
    This function opens a Selenium driver, goes to the cineman.ch Showtimes page.
    Then it scrapes the entries for a specified city.

    Optional argument:
    - cities: tuple of strings, specifies the cities for which showtimes will be scraped, defaults to ('Zürich')

    Returns:
    - content: html code of the page scraped with BeautifulSoup
    """
    # Open the driver and go to the page with the showtimes
    options = FirefoxOptions()
    options.add_argument("--headless")
    driver = webdriver.Firefox(options=options)
    driver.get("https://www.cineman.ch/en/showtimes/city/")
    time.sleep(15)  # have to wait for the advertisement to end

    # Click the cookie button
    cookie_button = driver.find_element_by_class_name("cc-btn")
    cookie_button.click()
    time.sleep(5)

    # Sort the showtimes by time
    sorting_buttons = driver.find_elements_by_class_name("text-overflow-hidden")
    sorting_buttons[2].click()

    # Click the region dropdown, select the city and save
    region_dropdown = driver.find_element_by_class_name("selectize-control")
    region_dropdown.click()

    input_div = driver.find_elements_by_xpath('//input[@type="text"]')
    for city in cities:
        input_div[6].send_keys(city)
        input_div[6].send_keys(Keys.RETURN)

    save_button = driver.find_element_by_class_name("select-region-save")
    save_button.click()

    # Scrape the content and close the driver
    content = BeautifulSoup(driver.page_source, features="html.parser")
    driver.close()

    return content


def format_cineman_content(html_content):
    """
    This function takes in content scraped from cineman.ch with BeautifulSoup and creates a dataframe from it.

    Required arguments:
    - content: html contents scraped from cineman.ch

    Returns:
    - movie_program_df: pandas dataframe containing the scraped data
    """
    movies = html_content.findAll("div", {"class": "col-xs-12 col-sm-9"})

    # Now create the data frame
    # Initialize the lists to store the details for each movie screening
    movies_list = []
    genres_list = []
    cinemas_list = []
    places_list = []
    age_limits = []
    all_showtimes_lists = []
    all_languages_lists = []

    for movie in movies:
        # Movie title
        title = movie.find("h4").get_text()
        movies_list.append(title)

        # Movie genre
        genre = movie.find("p").get_text()
        genres_list.append(genre)

        # Cinemas and place
        cinemas = movie.findAll("h5")
        cinema_names = []
        places = []

        for cinema in cinemas:
            cinema_name = cinema.find("em").get_text()
            cinema_names.append(cinema_name)
            place = cinema.findAll("a")[1].get_text()
            places.append(place)

        cinemas_list.append(cinema_names)
        places_list.append(places)

        # Age limit
        age_links = movie.findAll("a", {"class": "link"})
        age_limit = age_links[-1].get_text()
        if age_limit == "Reservation":
            age_limit = age_links[-2].get_text()
        if age_limit.find("Y.") == -1:
            age_limits.append("unknown")
        else:
            age_limits.append(age_limit)

        # Showtimes and languages
        showtimes_list_div = movie.find("div", {"class": "showtimes-list"})
        showtimes_string = showtimes_list_div.prettify().split("h5")
        showtimes_list = []
        languages_list = []

        for string in showtimes_string:
            strings = re.sub('<[^<]+?>\n', '', string).split(" ")
            showtimes = []
            languages = []

            for s in strings:
                s = s.strip("<></–)")
                s = re.sub("\t", "", s)
                s = s.strip()

                if is_time_format(s):
                    showtimes.append(s)

                elif (s.find("/") != -1 and s.find("Y.") == -1) or s in ["G", "F", "O", "I", "E"]:
                    languages.append(s)

            if showtimes:
                showtimes_list.append(showtimes)
            if languages:
                languages_list.append(languages)

        if showtimes_list:
            all_showtimes_lists.append(showtimes_list)
        if languages_list:
            all_languages_lists.append(languages_list)

    # Initializing the dictionary to store the lists
    all_info_dict = dict()

    all_info_dict["movie"] = movies_list
    all_info_dict["genre"] = genres_list
    all_info_dict["age_limit"] = age_limits
    all_info_dict["language"] = all_languages_lists
    all_info_dict["showtime"] = all_showtimes_lists
    all_info_dict["date"] = f'{date.today()}'
    all_info_dict["cinema"] = cinemas_list
    all_info_dict["place"] = places_list

    movie_program_df = pd.DataFrame(all_info_dict).explode(["cinema", "showtime", "place", "language"]).explode(
        ["showtime", "language"]).reset_index(drop=True)
    movie_program_df["dt_showtime"] = movie_program_df["date"] + " " + movie_program_df["showtime"]
    movie_program_df["dt_showtime"] = pd.to_datetime(movie_program_df["dt_showtime"], format='%Y-%m-%d %H:%M')
    movie_program_df["cinema_place"] = [f'{c} {p}' for c, p in
                                        zip(movie_program_df["cinema"], movie_program_df["place"])]

    return movie_program_df


def get_theatre_coordinates(showtimes_df, GOOGLE_CREDENTIALS_PATH, DATA_PATH_SHOWS):
    """
    This function takes in a dataframe with the names of movie theatres and the path to the credentials for the google api.
    It then requests the coordinates for the movie theatres via the api and stores them in a dataframe.

    Required arguments:
    - showtimes_df: pandas dataframe containing a column with movie theatre names and places
    - GOOGLE_CREDENTIALS_PATH: string, gives the path to the file with the google api credentials (user-dependent)
    - DATA_PATH_SHOWS: string, gives the path where the dataframe should be saved (user-dependent)

    Returns:
    - showtimes_df: pandas dataframe that was given as an input, but with the coordinates added in for each theatre
    """
    # Getting location data from the Google API
    key_json = json.load(open(GOOGLE_CREDENTIALS_PATH))
    gmaps_key = key_json["key"]

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json?"
    api_key = gmaps_key

    latitudes_list = []
    longitudes_list = []

    for theatre in showtimes_df["cinema_place"].unique():
        # text string on which to search
        query = theatre

        # get method of requests module, return response object
        req = requests.get(url + "query=" + query + "&key=" + api_key)

        # json method of response object: json format data -> python format data
        places_json = req.json()

        # now result contains list of nested dictionaries
        my_result = places_json["results"]

        # take a look at the first element
        latitude = my_result[0]["geometry"]["location"]["lat"]
        latitudes_list.append(latitude)

        longitude = my_result[0]["geometry"]["location"]["lng"]
        longitudes_list.append(longitude)

    theatre_location_dict = dict()
    theatre_location_dict["cinema_place"] = showtimes_df["cinema_place"].unique()
    theatre_location_dict["latitude"] = latitudes_list
    theatre_location_dict["longitude"] = longitudes_list

    theatre_locations_df = pd.DataFrame(theatre_location_dict)
    showtimes_df = pd.merge(showtimes_df, theatre_locations_df, how="left")

    showtimes_df.to_csv(DATA_PATH_SHOWS)