import google.generativeai as genai
genai.configure(api_key="여기에_실제_API_KEY")

model = genai.GenerativeModel("gemini-1.5-flash")
resp = model.generate_content("Summarize: It is 30°C with high humidity in Seoul, UV index 5.")
print(resp.text)