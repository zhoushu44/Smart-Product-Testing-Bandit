FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY guide.html /usr/share/nginx/html/guide.html

EXPOSE 7021

CMD ["nginx", "-g", "daemon off;"]
