import os

DATABASE_CONFIG = {
    "dbname": os.environ['dbname'],
    "user": os.environ['user'],
    "password": os.environ['password'],
    "host": os.environ['host'],
    "port": os.environ['port'],
}


LLM_CONFIG = {
    'base_url': "https://api.sambanova.ai/v1",
    'api_key' : "aa0a862a-5c6b-43b7-81cf-5d1a90d4751f"
    #  'api_key' : "b972fd9c-f064-4b63-8b1a-8c17bb52d8a3"
    # 'api_key': "f45e45ac-53fe-426f-b020-2d24a7306830"
    # 'api_key': "d0e931bd-97eb-4d9e-a980-80c8eab6857b"
}

LLAMA_API_URL = "http://localhost:11434"

schema = """
CREATE TABLE IF NOT EXISTS sales_data (
    business_line  character(60) COLLATE pg_catalog."default",
    branch  character(60) COLLATE pg_catalog."default",
    region   character(60) COLLATE pg_catalog."default",
    product             character(100) COLLATE pg_catalog."default",
    service             character(100) COLLATE pg_catalog."default",
    transaction_date    date,
    gross_amount        numeric(16,4),
    discounts           numeric(10,2),
    net_amount          numeric(16,4),
    cgst                numeric(10,2),
    sgst                numeric(10,2),
    igst                numeric(10,2),
    taxes               numeric(10,2),
    total_sales         numeric(16,4),
    amount_paid         numeric(16,4),
    cogs                numeric(10,2),
    sales_itm_qty       integer,
    manufacturer        character(100) COLLATE pg_catalog."default",
    quest_code          character(100) COLLATE pg_catalog."default",
    guest_id            character(100) COLLATE pg_catalog."default",
    guest_name          character(100) COLLATE pg_catalog."default",
    holiday             character(60) COLLATE pg_catalog."default",
    shipping_cost       numeric(10,2),
    cos                 numeric(10,2),
    margin              numeric(10,2)
);
"""
schema_name = "idea_clinic"
table_name = 'idea_clinic.sales_data'
few_shot_prompt = """
Translate the following natural language questions into PostgreSQL SQL queries based on the table structure provided in the schema.

1. What is the total sales amount for each product?
SQL:
SELECT product, SUM(gross_amount) AS total_sales
FROM idea_clinics.sales_data
GROUP BY product;

2. Get the total sales and gross amount for each region in the year 2023.
SQL:
SELECT region, SUM(net_amount) AS total_sales, SUM(gross_amount) AS total_gross_amount
FROM idea_clinics.sales_data
WHERE EXTRACT(YEAR FROM transaction_date) = 2023
GROUP BY region;

3. Find the total discounts applied for each product in the 'Electronics' category.
SQL:
SELECT product, SUM(discounts) AS total_discounts
FROM idea_clinics.sales_data
WHERE category = 'Electronics'
GROUP BY product;

4. List all products that had a net amount  greater than 1000 and a margin greater than 50.
SQL:
SELECT product, net_amount, margin
FROM idea_clinics.sales_data
WHERE net_amount > 1000 AND margin > 50;

5. Get the average sales quantity for each branch in 2023.
SQL:
SELECT branch, AVG(sale_itm_qty) AS avg_sales_qty
FROM idea_clinics.sales_data
WHERE EXTRACT(YEAR FROM transaction_date::DATE) = 2023
GROUP BY branch;

6. Show the total sales, taxes, and shipping costs for each product in the 'Hyderabad' region.
SQL:
SELECT product, SUM(gross_amount) AS total_sales, SUM(taxes) AS total_taxes, SUM(shipping_cost) AS total_shipping_cost
FROM idea_clinics.sales_data
WHERE region = 'Hyderabad'
GROUP BY product;

7. Find the region with the highest net amount and its associated gross amount.
SQL:
SELECT region, SUM(net_amount) AS total_net_amount, SUM(gross_amount) AS total_gross_amount
FROM idea_clinics.sales_data
GROUP BY region
ORDER BY total_net_amount DESC
LIMIT 1;

8. Get the total cost of goods sold (COGS) for each service type.
SQL:
SELECT service, SUM(cogs) AS total_cogs
FROM idea_clinics.sales_data
GROUP BY service;

9. Retrieve the branch, business line, region, total sales, and margin for each transaction where the business line is either Clinic Sales and Pharmacy Sales, and the net amount is greater than 500.
SQL:
SELECT
    branch,
    business_line,
    region,
    total_sales,
    margin,
    net_amount
FROM
        		idea_clinics.sales_data
WHERE
    business_line IN ('Clinic Sales', 'Pharmacy Sales')
    AND net_amount > 500;

10. Find the total sales for each service type by region.
SQL:
SELECT service, region, SUM(net_amount) AS total_sales
FROM    idea_clinics.sales_data
GROUP BY service, region;

11. Get top 3 products sold by region
SQL:
WITH region_products AS (
        	SELECT
        		"region",
        		"product",
        		SUM("gross_amount") AS total_gross_amount
        	FROM
        		idea_clinics.sales_data
        	GROUP BY
        		"region",
        		"product"
        )
        SELECT
        	"region",
        	"product",
        	total_gross_amount
        FROM
        	(
        		SELECT
        			"region",
        			"product",
        			total_gross_amount,
        			ROW_NUMBER() OVER(PARTITION BY "region" ORDER BY total_gross_amount DESC) as rn
        		FROM
        			region_products
        	) rp
        WHERE
        	rn <= 3
"""

