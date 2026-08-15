# Shelfie

A mobile app that turns a photo of a bookshelf into a structured personal library.

Expo (React Native) client, Django REST Framework API, a pretrained local vision model for spine detection, and a hosted vision-language model for reading spines.

## The flow

1. User takes or picks a photo of a bookshelf in an Expo app.
2. The photo goes to a Django REST API.
3. The backend uses a pretrained local vision model to find the individual book spines in the image.
4. The backend uses a hosted vision-language model to read title and author off the spines.
5. Each read is matched against your catalog (see below) to a canonical catalog entry, with a confidence score.
6. The app shows the result. High-confidence matches can be added directly. Low-confidence and unmatched books go to a review step where the user confirms, corrects, or discards them.
7. Confirmed books persist to the user's library, viewable as a list.
